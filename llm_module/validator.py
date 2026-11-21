import json
import re
from schema import LLMReport

CVE_PATTERN = re.compile(r"(CVE-\d{4}-\d{4,}|GHSA-[a-z0-9]{4}-[a-z0-9]{4}-[a-z0-9]{4})", re.IGNORECASE)

def generate_finding_id(f: dict) -> str:
    """Генерирует уникальный ID для находки на основе её полей."""
    if f.get("category") == "SCA":
        return f"sca_{f.get('group')}_{f.get('artifact')}_{f.get('version')}_{f.get('advisoryId')}"
    else:
        return f"{f.get('category').lower()}_{f.get('path')}_{f.get('class')}_{f.get('line')}"

def validate_response(raw: str, findings: list[dict]) -> tuple[LLMReport | None, list[str]]:
    errors = []

    # 1. Извлекаем JSON
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        m = re.search(r"```(?:json)?\s*([\s\S]*?)```", raw)
        if m:
            try:
                data = json.loads(m.group(1))
            except json.JSONDecodeError:
                return None, ["Ответ не является валидным JSON"]
        else:
            return None, ["Ответ не является валидным JSON"]

    # 2. Проверяем структуру через Pydantic
    try:
        report = LLMReport.model_validate(data)
    except Exception as e:
        return None, [f"Ошибка структуры ответа: {e}"]

    # 3. Генерируем валидные ID для входных находок
    valid_ids = {generate_finding_id(f) for f in findings}
    
    for item in report.items:
        if item.finding_id not in valid_ids:
            errors.append(f"Выдуманный ID находки: {item.finding_id}. Доступные ID: {list(valid_ids)[:3]}...")

    # 4. Проверяем на выдуманные CVE/GHSA
    allowed_advisories = set()
    for f in findings:
        adv = f.get("advisoryId")
        if adv:
            allowed_advisories.add(adv.upper())
            
    found_advisories = set(adv.upper() for adv in CVE_PATTERN.findall(raw))
    fake_advisories = found_advisories - allowed_advisories
    
    if fake_advisories:
        errors.append(f"Выдуманные CVE/GHSA (которых нет во входных данных): {sorted(fake_advisories)}")

    if errors:
        return None, errors
        
    return report, []