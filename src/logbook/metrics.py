from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class MetricSample:
    name: str
    value: int | float
    labels: dict[str, str] = field(default_factory=dict)
    help_text: str | None = None
    metric_type: str = "gauge"


def render_prometheus_metrics(samples: list[MetricSample]) -> str:
    lines: list[str] = []
    emitted_metadata: set[str] = set()
    for sample in samples:
        if sample.name not in emitted_metadata:
            if sample.help_text:
                lines.append(f"# HELP {sample.name} {_escape_help(sample.help_text)}")
            lines.append(f"# TYPE {sample.name} {sample.metric_type}")
            emitted_metadata.add(sample.name)
        lines.append(f"{sample.name}{_render_labels(sample.labels)} {_render_value(sample.value)}")
    return "\n".join(lines) + "\n"


def _render_labels(labels: dict[str, str]) -> str:
    if not labels:
        return ""
    rendered = ",".join(
        f'{key}="{_escape_label(value)}"'
        for key, value in sorted(labels.items())
    )
    return "{" + rendered + "}"


def _render_value(value: int | float) -> str:
    if isinstance(value, float) and not value.is_integer():
        return f"{value:.3f}".rstrip("0").rstrip(".")
    return str(int(value))


def _escape_label(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _escape_help(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", " ")
