"""
FastAPI бэкенд для Sales Outreach Automation
Запуск: uvicorn api:app --reload --port 8000
"""

import json
import asyncio
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Optional
import sys
import os

# Добавь путь к проекту
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

app = FastAPI(title="Sales Outreach Automation API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Описание узлов графа для красивого отображения
NODE_DESCRIPTIONS = {
    "get_new_leads":                  "Загрузка лидов из CRM",
    "check_for_remaining_leads":      "Проверка очереди лидов",
    "fetch_linkedin_profile_data":    "Исследование LinkedIn профиля",
    "review_company_website":         "Анализ сайта компании",
    "collect_company_information":    "Сбор данных о компании",
    "analyze_blog_content":           "Анализ блога компании",
    "analyze_social_media_content":   "Анализ социальных сетей",
    "analyze_recent_news":            "Поиск последних новостей",
    "generate_digital_presence_report": "Отчёт о цифровом присутствии",
    "generate_full_lead_research_report": "Глобальный аналитический отчёт",
    "score_lead":                     "Квалификация и оценка лида",
    "generate_custom_outreach_report":"Персонализированный outreach отчёт",
    "create_outreach_materials":      "Создание outreach материалов",
    "generate_personalized_email":    "Генерация персонального письма",
    "generate_interview_script":      "Скрипт для звонка (SPIN)",
    "await_reports_creation":         "Ожидание завершения отчётов",
    "save_reports_to_google_docs":    "Сохранение в Google Docs",
    "update_CRM":                     "Обновление CRM статуса",
}

class LeadInput(BaseModel):
    name: str
    email: str
    phone: Optional[str] = ""
    address: Optional[str] = ""
    use_airtable: bool = False  # если False — создаём лида из формы напрямую


def send_event(event_type: str, data: dict) -> str:
    """Форматирует SSE событие"""
    return f"data: {json.dumps({'type': event_type, **data})}\n\n"


async def run_pipeline_stream(lead_input: LeadInput):
    """Генератор SSE событий — запускает граф и стримит прогресс"""
    try:
        from dotenv import load_dotenv
        load_dotenv()

        from src.tools.leads_loader.airtable import AirtableLeadLoader
        from src.graph import OutReachAutomation

        yield send_event("start", {"message": "Запуск пайплайна..."})
        await asyncio.sleep(0.1)

        # Инициализация CRM загрузчика
        loader = AirtableLeadLoader(
            access_token=os.getenv("AIRTABLE_ACCESS_TOKEN"),
            base_id=os.getenv("AIRTABLE_BASE_ID"),
            table_name=os.getenv("AIRTABLE_TABLE_NAME"),
        )

        # Если не используем Airtable — создаём запись вручную
        if not lead_input.use_airtable:
            yield send_event("info", {"message": f"Создание лида: {lead_input.name}"})
            record = loader.table.create({
                "Name": lead_input.name,
                "Email": lead_input.email,
                "Phone": lead_input.phone,
                "Address": lead_input.address,
                "Status": "NEW"
            })
            lead_id = record["id"]
            yield send_event("info", {"message": f"Лид создан в Airtable (ID: {lead_id})"})
            inputs = {"leads_ids": [lead_id]}
        else:
            inputs = {"leads_ids": []}

        # Строим граф
        automation = OutReachAutomation(loader)
        config = {"recursion_limit": 100}

        yield send_event("graph_start", {"message": "Граф запущен"})

        # Стримим выполнение графа узел за узлом
        completed_nodes = []
        reports_data = []

        for chunk in automation.app.stream(inputs, config, stream_mode="updates"):
            for node_name, node_output in chunk.items():
                description = NODE_DESCRIPTIONS.get(node_name, node_name)
                completed_nodes.append(node_name)

                yield send_event("node_complete", {
                    "node": node_name,
                    "description": description,
                    "completed": completed_nodes.copy()
                })

                # Если узел вернул отчёты — передаём их на фронт
                if node_output and "reports" in node_output:
                    for report in node_output.get("reports", []):
                        if hasattr(report, "title") and hasattr(report, "content"):
                            reports_data.append({
                                "title": report.title,
                                "content": report.content[:2000],  # первые 2000 символов
                                "is_markdown": report.is_markdown
                            })
                            yield send_event("report_ready", {
                                "title": report.title,
                                "preview": report.content[:300]
                            })

                # Score лида
                if node_output and "lead_score" in node_output:
                    score = node_output["lead_score"]
                    yield send_event("score", {"value": str(score)[:200]})

                # Ссылки на Google Docs
                if node_output and "reports_folder_link" in node_output:
                    link = node_output["reports_folder_link"]
                    if link:
                        yield send_event("drive_link", {"url": link})

                if node_output and "custom_outreach_report_link" in node_output:
                    link = node_output["custom_outreach_report_link"]
                    if link:
                        yield send_event("outreach_link", {"url": link})

                await asyncio.sleep(0.05)

        yield send_event("done", {
            "message": "Пайплайн завершён успешно",
            "reports": reports_data
        })

    except Exception as e:
        yield send_event("error", {"message": str(e)})


@app.post("/run")
async def run_pipeline(lead: LeadInput):
    """Запускает пайплайн и стримит прогресс через SSE"""
    return StreamingResponse(
        run_pipeline_stream(lead),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        }
    )


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/nodes")
async def get_nodes():
    """Возвращает список всех узлов графа для визуализации"""
    return {"nodes": NODE_DESCRIPTIONS}
