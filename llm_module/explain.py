import json
from provider import chat
from validator import validate_response, generate_finding_id
from schema import LLMReport

SYSTEM_PROMPT = """Ты — помощник по безопасности кода.
Тебе на вход дадут JSON-массив результатов сканирования безопасности.
Каждая находка имеет свой уникальный ID в поле "id".
Твоя задача — для каждой находки:
1. Объясни риск простым языком.
2. Оцени уровень критичности (LOW/MEDIUM/HIGH/CRITICAL).
3. Дай конкретные шаги исправления.

СТРОГИЕ ПРАВИЛА:
- Поле "finding_id" должно точно совпадать с "id" из входных данных.
- Не придумывай номера CVE или GHSA, если их нет в поле "advisoryId" входной находки.
- Выведи ТОЛЬКО валидный JSON без markdown-оберток.
Формат: 
{
  "summary": "Краткое резюме",
  "overall_severity": "HIGH",
  "items": [
    {
      "finding_id": "id_из_входа",
      "risk_explanation": "...",
      "severity_review": "...",
      "remediation": "..."
    }
  ]
}
"""

def explain_findings(findings: list[dict], max_retries: int = 2) -> LLMReport:
    # Подготавливаем входные данные: обогащаем их ID для LLM
    enriched_findings = []
    for f in findings:
        enriched = f.copy()
        enriched["id"] = generate_finding_id(f)
        enriched_findings.append(enriched)

    user_content = f"Результаты сканирования:\n{json.dumps(enriched_findings, ensure_ascii=False, indent=2)}"
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_content}
    ]

    last_errors = []
    for attempt in range(max_retries + 1):
        print(f"[*] Запрос к LLM (попытка {attempt + 1})...")
        raw = chat(messages)
        
        report, errors = validate_response(raw, findings)
        if report is not None:
            print("[+] Ответ прошел валидацию!")
            return report

        last_errors = errors
        print(f"[-] Ошибка валидации: {errors}")
        
        if attempt < max_retries:
            messages.append({"role": "assistant", "content": raw})
            messages.append({
                "role": "user", 
                "content": f"Твой ответ не прошел валидацию. Ошибки:\n{chr(10).join(errors)}\nИсправь их и выдай только валидный JSON."
            })

    raise ValueError(f"LLM не смогла выдать валидный ответ. Ошибки: {last_errors}")