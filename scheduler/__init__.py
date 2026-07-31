"""调度器包：可定时/可事件驱动的调度实体 + 真实钩子体。"""
from scheduler.scheduler import (
    Scheduler, SchedContext, ScheduleSpec, Job, MaterialEvent,
    PushSink, NullPushSink, ConsolePushSink, FilePushSink,
    WebhookPushSink, MultiPushSink, MATERIAL_EVENT_KINDS, is_material_event,
)
from scheduler.jobs import (
    make_default_jobs, post_market_job, pre_market_job, weekly_job,
    event_job, run_research_for_ticker,
)

__all__ = [
    "Scheduler", "SchedContext", "ScheduleSpec", "Job", "MaterialEvent",
    "PushSink", "NullPushSink", "ConsolePushSink", "FilePushSink",
    "WebhookPushSink", "MultiPushSink", "MATERIAL_EVENT_KINDS", "is_material_event",
    "make_default_jobs", "post_market_job", "pre_market_job", "weekly_job",
    "event_job", "run_research_for_ticker",
]
