from rich.console import Group
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
)

current_system_progress = Progress(
    TimeElapsedColumn(),
    TextColumn("{task.description}"),
)

step_progress = Progress(
    TextColumn("  "),
    TimeElapsedColumn(),
    TextColumn("[bold purple]{task.fields[action]}"),
    SpinnerColumn("simpleDots"),
)

system_steps_progress = Progress(
    TextColumn("[bold blue]Progress for system {task.fields[name]}: {task.percentage:.0f}%"),
    BarColumn(),
    TextColumn("({task.completed} of {task.total} steps done)"),
)

transport_status_progress = Progress(
    TextColumn("  "),
    TextColumn("[bold cyan]{task.fields[msg]}"),
)

transport_file_progress = Progress(
    TextColumn("  "),
    TextColumn("[bold green]File Upload"),
    BarColumn(),
    TextColumn("{task.completed}/{task.total}"),
)

overall_progress = Progress(TimeElapsedColumn(), BarColumn(), TextColumn("{task.description}"))

progress_group = Group(
    Panel(
        Group(
            current_system_progress,
            step_progress,
            transport_status_progress,
            transport_file_progress,
            system_steps_progress,
        )
    ),
    overall_progress,
)

transport_status_task_id = None
transport_file_task_id = None


def init_live_tasks():
    global transport_status_task_id
    global transport_file_task_id
    transport_status_task_id = transport_status_progress.add_task("", msg="", visible=False)
    transport_file_task_id = transport_file_progress.add_task("", total=0, visible=False)


def set_transport_status(message):
    if transport_status_task_id is None:
        return
    visible = bool(message)
    transport_status_progress.update(transport_status_task_id, msg=message, visible=visible)


def begin_transport_file_progress(total):
    if transport_file_task_id is None:
        return
    transport_file_progress.update(
        transport_file_task_id, total=max(0, total), completed=0, visible=bool(total)
    )


def advance_transport_file_progress(step=1):
    if transport_file_task_id is None:
        return
    transport_file_progress.update(transport_file_task_id, advance=step)


def end_transport_file_progress():
    if transport_file_task_id is None:
        return
    transport_file_progress.update(transport_file_task_id, visible=False)


def complete_transport_file_progress():
    if transport_file_task_id is None:
        return
    task = transport_file_progress.tasks[transport_file_task_id]
    transport_file_progress.update(transport_file_task_id, completed=task.total)


def hide_transport_tasks():
    end_transport_file_progress()
    set_transport_status("")
