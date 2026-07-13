from datetime import datetime, timezone

from .exceptions import StepExecutionError
from .models import ConversionPlan, PipelineContext, PipelineLogEntry
from .registry import StepRegistry


class PipelineRunner:
    def run(self, plan: ConversionPlan, registry: StepRegistry, context: PipelineContext) -> PipelineContext:
        for step_name in plan.required_steps:
            started_at = datetime.now(timezone.utc)
            context.logs.append(PipelineLogEntry(step_name, "started", started_at, message="step started"))
            try:
                registry.get(step_name).run(context)
            except Exception as exc:
                finished_at = datetime.now(timezone.utc)
                context.logs.append(PipelineLogEntry(step_name, "failed", started_at, finished_at, str(exc)))
                raise StepExecutionError(f"Step {step_name!r} failed: {exc}") from exc
            finished_at = datetime.now(timezone.utc)
            context.logs.append(PipelineLogEntry(step_name, "completed", started_at, finished_at, "step completed"))
        return context

