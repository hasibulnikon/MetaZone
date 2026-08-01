"""Builds the end-of-run processing report (TXT format, per v1.0 spec)."""
import os, datetime


def build_report_text(summary):
    lines = [
        "META ZONE — SMART WORKFLOW REPORT",
        "=" * 40,
        f"Generated: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        f"Total Images:        {summary.get('total', 0)}",
        f"Processed:            {summary.get('processed', 0)}",
        f"Good:                 {summary.get('good', 0)}",
        f"Needs Review:         {summary.get('review', 0)}",
        f"Rejected:             {summary.get('rejected', 0)}",
        f"Metadata Generated:   {summary.get('metadata_generated', 0)}",
        f"Embedded:             {summary.get('embedded', 0)}",
        f"Average Metadata Score: {summary.get('avg_score', 0):.0f}%",
        f"Processing Time:      {summary.get('elapsed', '—')}",
        f"API Provider(s) Used: {summary.get('providers', '—')}",
        f"Errors:               {summary.get('errors', 0)}",
        "",
    ]
    err_list = summary.get("error_list") or []
    if err_list:
        lines.append("ERROR DETAILS")
        lines.append("-" * 40)
        for fn, msg in err_list[:200]:
            lines.append(f"  {fn}: {msg}")
    return "\n".join(lines)


def write_report(folder, summary):
    logs_dir = os.path.join(folder, "Logs")
    os.makedirs(logs_dir, exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(logs_dir, f"smart_workflow_report_{ts}.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write(build_report_text(summary))
    return path
