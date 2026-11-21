from pydantic import BaseModel, Field
from typing import List

class LLMFindingRef(BaseModel):
    finding_id: str = Field(description="Уникальный ID находки (генерируется нами на основе path/line/class)")
    risk_explanation: str = Field(description="Объяснение риска простым языком.")
    severity_review: str = Field(description="Оценка критичности: LOW/MEDIUM/HIGH/CRITICAL.")
    remediation: str = Field(description="Конкретные шаги по исправлению.")

class LLMReport(BaseModel):
    summary: str = Field(description="Общее резюме всех найденных проблем.")
    items: List[LLMFindingRef]
    overall_severity: str = Field(description="Общий уровень критичности.")