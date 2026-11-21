import sys
import json
from explain import explain_findings

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Использование: python cli.py <путь_к_отчету.json>")
        sys.exit(1)

    input_file = sys.argv[1]
    
    try:
        with open(input_file, "r", encoding="utf-8") as f:
            data = json.load(f)
            # Извлекаем массив находок
            findings = data.get("findings", [])
            if not findings:
                print("Входной файл не содержит находок (findings).")
                sys.exit(0)
    except Exception as e:
        print(f"Ошибка чтения файла: {e}")
        sys.exit(1)

    try:
        report = explain_findings(findings)
        
        print("\n=== РЕЗУЛЬТАТ ===")
        print(json.dumps(report.model_dump(), ensure_ascii=False, indent=2))
        
        with open("report_output.json", "w", encoding="utf-8") as f:
            json.dump(report.model_dump(), f, ensure_ascii=False, indent=2)
        print("\n[+] Отчет сохранен в report_output.json")
        
    except Exception as e:
        print(f"\n[!] Ошибка: {e}")