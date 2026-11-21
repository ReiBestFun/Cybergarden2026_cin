"""Run the static-analysis nodes against one prepared local snapshot."""

from __future__ import annotations

import argparse
import logging
import math
import multiprocessing
import os
import sys
import tempfile
from dataclasses import replace
from multiprocessing.connection import Connection
from pathlib import Path
from queue import Empty, Queue
from threading import Thread
from time import monotonic
from typing import Callable

from .analyzers import Snapshot, VulnerabilityCatalog, scan_sast, scan_sca, scan_secrets
from .analyzers.models import Category, FindingVerification, ScanError, Warning
from .analyzers.sast import accepts_sast_source
from .analyzers.sca import accepts_sca_source, catalog_missing
from .analyzers.secrets import accepts_secrets_source
from .analyzers.verification import verify_findings
from .reports import NodeResult, RunReport, merge_findings, readable_report, submission_json

ALL_SCANS: tuple[Category, ...] = ("secrets", "sca", "sast")
_SCAN_FAILED = Warning("SCAN_INTERNAL_ERROR", "В анализаторе произошла внутренняя ошибка. Проверьте журнал воркера.")
_PROCESS_EXIT = Warning("SCAN_PROCESS_EXIT", "Процесс анализатора завершился без полного результата.")
_SCAN_TIMEOUT = Warning("SCAN_TIMEOUT", "Тестер превысил лимит времени.")
_VERIFICATION_TIMEOUT = FindingVerification("needs_review", "VERIFICATION_TIMEOUT", "Дополнительная проверка превысила лимит времени.")
_VERIFICATION_FAILED = FindingVerification("needs_review", "VERIFICATION_FAILED", "Дополнительная проверка не вернула результат.")
log = logging.getLogger(__name__)


def _scan_node(
    category: Category, snapshot: Snapshot, catalog_path: Path | None, output: Connection, run_id: str
) -> None:
    catalog = None
    try:
        if category == "secrets":
            result = scan_secrets(snapshot)
        elif category == "sast":
            result = scan_sast(snapshot)
        else:
            if catalog_path is None:
                raise catalog_missing()
            catalog = VulnerabilityCatalog.from_file(catalog_path)
            result = scan_sca(snapshot, catalog)
        outcome = NodeResult(category, "succeeded", result)
    except ScanError as error:
        outcome = NodeResult(category, "failed", warning=error.warning)
    except Exception as error:
        log.error("Run %s, scanner %s, scan: %s", run_id, category, type(error).__name__)
        outcome = NodeResult(category, "failed", warning=_SCAN_FAILED)
    with output:
        output.send(outcome)
        if outcome.result is not None and outcome.result.findings:
            try:
                verdicts = verify_findings(snapshot, outcome.result.findings, catalog)
            except Exception as error:
                log.error("Run %s, scanner %s, verification: %s", run_id, category, type(error).__name__)
                verdicts = None
            output.send(verdicts)


def _receive_outcome(reader: Connection, received: Queue, category: Category) -> None:
    try:
        outcome = reader.recv()
    except Exception:
        outcome = NodeResult(category, "failed", warning=_PROCESS_EXIT)
    received.put(outcome)
    if outcome.result is not None and outcome.result.findings:
        try:
            received.put(reader.recv())
        except Exception:
            received.put(None)


def _verified_outcome(outcome: NodeResult, received: Queue, deadline: float) -> NodeResult:
    result = outcome.result
    if result is None or not result.findings:
        return outcome
    try:
        verdicts = received.get(timeout=max(0, deadline - monotonic()))
    except Empty:
        verdicts = (_VERIFICATION_TIMEOUT,) * len(result.findings)
    if verdicts is None or len(verdicts) != len(result.findings):
        verdicts = (_VERIFICATION_FAILED,) * len(result.findings)
    findings = tuple(
        replace(finding, verification=verdict)
        for finding, verdict in zip(result.findings, verdicts, strict=True)
    )
    return replace(outcome, result=replace(result, findings=findings))


def run_scan(
    category: Category, snapshot: Snapshot, catalog: Path | None, timeout: float, *, run_id: str = "local"
) -> NodeResult:
    deadline = monotonic() + timeout
    if category == "secrets":
        snapshot = snapshot.select(accepts_secrets_source)
    elif category == "sca":
        snapshot = snapshot.select(accepts_sca_source)
    else:
        snapshot = snapshot.select(accepts_sast_source)
    context = multiprocessing.get_context("spawn")
    reader, writer = context.Pipe(duplex=False)
    process = context.Process(target=_scan_node, args=(category, snapshot, catalog, writer, run_id), daemon=True)
    with reader, writer:
        try:
            process.start()
        except OSError as error:
            log.error("Run %s, scanner %s, start: %s", run_id, category, type(error).__name__)
            process.close()
            return NodeResult(category, "failed", warning=_PROCESS_EXIT)
        writer.close()
        received = Queue(maxsize=2)
        receive = Thread(target=_receive_outcome, args=(reader, received, category), daemon=True)
        receive.start()
        try:
            outcome = received.get(timeout=max(0, deadline - monotonic()))
        except Empty:
            outcome = NodeResult(category, "failed", warning=_SCAN_TIMEOUT)
        else:
            outcome = _verified_outcome(outcome, received, deadline)
        finally:
            process.join(min(0.1, max(0, deadline - monotonic())))
            if process.is_alive():
                process.kill()
                process.join()
            # On Windows a killed child can leave an overlapped pipe read pending.
            # The daemon reader must not extend a scan past its deadline.
            receive.join(timeout=0.1)
            if process.exitcode not in (0, None):
                log.warning("Run %s, scanner %s, process exit: %s", run_id, category, process.exitcode)
            process.close()
    return outcome


def run_analysis(
    directory: Path,
    sha: str,
    catalog: Path | None,
    *,
    scans: tuple[Category, ...] = ALL_SCANS,
    timeout: float = 30,
    save_node: Callable[[NodeResult], None] | None = None,
) -> RunReport:
    """Pass a terminal node status and its findings together to the optional storage callback."""
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("Таймаут должен быть положительным числом.")
    if not scans or len(set(scans)) != len(scans) or any(scan not in ALL_SCANS for scan in scans):
        raise ValueError("Укажите неповторяющиеся типы анализаторов.")

    nodes: list[NodeResult] = []
    try:
        snapshot = Snapshot.from_directory(directory, sha)
    except ScanError as error:
        for scan in scans:
            node = NodeResult(scan, "failed", warning=error.warning)
            if save_node is not None:
                save_node(node)
            nodes.append(node)
        return merge_findings(sha if error.warning.code != "INVALID_SHA" else "", tuple(nodes))
    for scan in scans:
        node = run_scan(scan, snapshot, catalog, timeout)
        if save_node is not None:
            save_node(node)
        nodes.append(node)
    return merge_findings(snapshot.sha, tuple(nodes))


def _write_report(path: Path, text: str) -> None:
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, delete=False
    ) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(text)
            stream.close()
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Статический анализ локального снимка учебного проекта.")
    parser.add_argument("directory", type=Path, help="Корень проекта в подготовленном снимке")
    parser.add_argument("--sha", required=True, help="Полный SHA коммита снимка")
    parser.add_argument("--catalog", type=Path, help="JSON-справочник организаторов для SCA")
    parser.add_argument("--json-out", type=Path, required=True, help="Путь к JSON для сдачи")
    parser.add_argument("--markdown-out", type=Path, help="Путь к читаемому отчёту")
    parser.add_argument("--timeout", type=float, default=30, help="Лимит секунд на одну проверку")
    args = parser.parse_args()
    if args.markdown_out and args.markdown_out.resolve() == args.json_out.resolve():
        parser.error("Для JSON и Markdown требуются разные выходные пути.")
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error("Таймаут должен быть положительным числом.")
    report = run_analysis(args.directory, args.sha, args.catalog, timeout=args.timeout)
    markdown = readable_report(report)
    try:
        if report.status != "failed":
            _write_report(args.json_out, submission_json(report))
        if args.markdown_out:
            _write_report(args.markdown_out, markdown)
        else:
            print(markdown, end="")
    except OSError:
        print("Не удалось записать отчёт.", file=sys.stderr)
        return 2
    return {"succeeded": 0, "partial": 1, "failed": 2}[report.status]


if __name__ == "__main__":
    raise SystemExit(main())
