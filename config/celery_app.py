from celery import Celery

from config.settings import settings

celery_app = Celery("devops_agent", broker=settings.celery_broker_url, include=["jobs.webhooks_job"])
