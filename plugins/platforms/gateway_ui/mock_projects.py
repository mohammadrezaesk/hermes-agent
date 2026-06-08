"""Mock project portfolio data for the gateway-ui projects page."""

from __future__ import annotations

from typing import Any, Dict, List

MOCK_PROJECTS: List[Dict[str, Any]] = [
    {
        "id": "prj-erp-01",
        "code": "PRJ-ERP-01",
        "title": "مهاجرت ERP به ابر اپکس",
        "description": (
            "انتقال ماژول‌های مالی، انبار و خرید از سیستم قدیمی به پلتفرم ابری "
            "با حداقل اختلال در عملیات روزانه."
        ),
        "status": "at_risk",
        "progress": 62,
        "budget_used": 71,
        "lead": "مریم رضایی",
        "due_date": "2026-08-15",
        "teams": ["فناوری اطلاعات", "مالی", "عملیات"],
    },
    {
        "id": "prj-q4-ops-02",
        "code": "PRJ-Q4-OPS-02",
        "title": "بهینه‌سازی زنجیره تأمین",
        "description": (
            "کاهش زمان تحویل و هزینه حمل با بازطراحی مسیرهای توزیع و "
            "یکپارچه‌سازی داده‌های انبار در سه منطقه."
        ),
        "status": "on_track",
        "progress": 45,
        "budget_used": 38,
        "lead": "علی محمدی",
        "due_date": "2026-10-30",
        "teams": ["عملیات", "لجستیک", "تحلیل داده"],
    },
    {
        "id": "prj-data-03",
        "code": "PRJ-DATA-03",
        "title": "پلتفرم یکپارچه‌سازی داده",
        "description": (
            "ایجاد دریاچه داده مرکزی برای گزارش‌دهی مدیریتی و حذف "
            "گزارش‌های دستی بین واحدهای مختلف سازمان."
        ),
        "status": "off_track",
        "progress": 28,
        "budget_used": 52,
        "lead": "سارا احمدی",
        "due_date": "2026-06-20",
        "teams": ["فناوری اطلاعات", "تحلیل داده", "مالی"],
    },
    {
        "id": "prj-hr-04",
        "code": "PRJ-HR-04",
        "title": "دیجیتالی‌سازی فرآیندهای منابع انسانی",
        "description": (
            "خودکارسازی درخواست مرخصی، ارزیابی عملکرد و آموزش کارکنان "
            "در یک پورتال واحد برای تمام شعب."
        ),
        "status": "on_track",
        "progress": 78,
        "budget_used": 65,
        "lead": "نیما کریمی",
        "due_date": "2026-09-01",
        "teams": ["منابع انسانی", "فناوری اطلاعات"],
    },
    {
        "id": "prj-mkt-05",
        "code": "PRJ-MKT-05",
        "title": "راه‌اندازی بازار B2B",
        "description": (
            "راه‌اندازی فروشگاه عمده‌فروشی آنلاین با اتصال به موجودی انبار "
            "و سیستم قیمت‌گذاری پویا برای مشتریان سازمانی."
        ),
        "status": "completed",
        "progress": 100,
        "budget_used": 94,
        "lead": "زهرا موسوی",
        "due_date": "2026-03-01",
        "teams": ["بازاریابی", "فروش", "فناوری اطلاعات"],
    },
    {
        "id": "prj-sec-06",
        "code": "PRJ-SEC-06",
        "title": "ارتقای امنیت زیرساخت",
        "description": (
            "استقرار احراز هویت چندعاملی، ممیزی دسترسی‌ها و سخت‌سازی "
            "سرویس‌های در معرض اینترنت طبق استاندارد ISO 27001."
        ),
        "status": "on_track",
        "progress": 55,
        "budget_used": 48,
        "lead": "حسین نوری",
        "due_date": "2026-11-15",
        "teams": ["امنیت", "فناوری اطلاعات", "عملیات"],
    },
]


def list_mock_projects() -> List[Dict[str, Any]]:
    from plugins.platforms.gateway_ui.project_store import get_project_store

    return get_project_store().list_projects()
