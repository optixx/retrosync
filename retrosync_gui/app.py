from pathlib import Path

import dearpygui.dearpygui as dpg

from retrosync_app import RetrosyncAppController


class RetrosyncGuiApp:
    ACTION_BUTTON_WIDTH = 120
    MATCH_TEXT_COLOR = (90, 180, 200)

    def __init__(self):
        self.controller = RetrosyncAppController()
        self._last_system_signature = None
        self._last_log_signature = None
        self._last_plan_signature = None
        self._last_error_signature = None

    def run(self):
        dpg.create_context()
        try:
            dpg.configure_app(docking=False, docking_space=False)
            self._build_theme()
            self._build_ui()
            self._autoload_initial_config()
            dpg.create_viewport(title="Retrosync", width=1400, height=900)
            dpg.setup_dearpygui()
            dpg.show_viewport()
            dpg.set_viewport_resize_callback(self._on_viewport_resize)
            self._sync_main_window_to_viewport()
            self._refresh(force=True)
            while dpg.is_dearpygui_running():
                self.controller.drain_events()
                self._refresh()
                dpg.render_dearpygui_frame()
            self.controller.cancel_run()
        finally:
            dpg.destroy_context()

    def _build_theme(self):
        with dpg.theme() as global_theme:
            with dpg.theme_component(dpg.mvAll):
                dpg.add_theme_style(dpg.mvStyleVar_WindowRounding, 6)
                dpg.add_theme_style(dpg.mvStyleVar_FrameRounding, 4)
                dpg.add_theme_style(dpg.mvStyleVar_ChildRounding, 4)
                dpg.add_theme_color(dpg.mvThemeCol_WindowBg, (22, 26, 30))
                dpg.add_theme_color(dpg.mvThemeCol_ChildBg, (18, 22, 25))
                dpg.add_theme_color(dpg.mvThemeCol_FrameBg, (31, 37, 42))
                dpg.add_theme_color(dpg.mvThemeCol_FrameBgHovered, (42, 64, 71))
                dpg.add_theme_color(dpg.mvThemeCol_Button, (38, 92, 102))
                dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, (46, 114, 126))
                dpg.add_theme_color(dpg.mvThemeCol_ButtonActive, (31, 75, 84))
                dpg.add_theme_color(dpg.mvThemeCol_Header, (32, 73, 82))
                dpg.add_theme_color(dpg.mvThemeCol_HeaderHovered, (43, 95, 107))
                dpg.add_theme_color(dpg.mvThemeCol_HeaderActive, (37, 80, 88))
                dpg.add_theme_color(dpg.mvThemeCol_Border, (52, 62, 68))
                dpg.add_theme_color(dpg.mvThemeCol_Text, (220, 226, 230))
                dpg.add_theme_color(dpg.mvThemeCol_TextDisabled, (130, 138, 144))
        dpg.bind_theme(global_theme)

        with dpg.theme() as action_button_theme:
            with dpg.theme_component(dpg.mvButton):
                dpg.add_theme_color(dpg.mvThemeCol_Button, (38, 92, 102))
                dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, (46, 114, 126))
                dpg.add_theme_color(dpg.mvThemeCol_ButtonActive, (31, 75, 84))
                dpg.add_theme_color(dpg.mvThemeCol_Text, (220, 226, 230))

        with dpg.theme() as disabled_button_theme:
            with dpg.theme_component(dpg.mvButton):
                dpg.add_theme_color(dpg.mvThemeCol_Button, (48, 48, 48))
                dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, (48, 48, 48))
                dpg.add_theme_color(dpg.mvThemeCol_ButtonActive, (48, 48, 48))
                dpg.add_theme_color(dpg.mvThemeCol_Text, (110, 110, 110))

        with dpg.theme() as cancel_button_theme:
            with dpg.theme_component(dpg.mvButton):
                dpg.add_theme_color(dpg.mvThemeCol_Button, (130, 52, 52))
                dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, (156, 64, 64))
                dpg.add_theme_color(dpg.mvThemeCol_ButtonActive, (112, 42, 42))
                dpg.add_theme_color(dpg.mvThemeCol_Text, (235, 230, 230))

        with dpg.theme() as cancel_disabled_button_theme:
            with dpg.theme_component(dpg.mvButton):
                dpg.add_theme_color(dpg.mvThemeCol_Button, (48, 48, 48))
                dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, (48, 48, 48))
                dpg.add_theme_color(dpg.mvThemeCol_ButtonActive, (48, 48, 48))
                dpg.add_theme_color(dpg.mvThemeCol_Text, (110, 110, 110))

        self._action_button_theme = action_button_theme
        self._disabled_button_theme = disabled_button_theme
        self._cancel_button_theme = cancel_button_theme
        self._cancel_disabled_button_theme = cancel_disabled_button_theme

    def _build_ui(self):
        with dpg.file_dialog(
            tag="config_file_dialog",
            show=False,
            width=900,
            height=600,
            callback=self._load_selected_config,
            cancel_callback=lambda: None,
            modal=True,
        ):
            dpg.add_file_extension(".conf", color=(90, 180, 200, 255))
            dpg.add_file_extension(".toml", color=(90, 180, 200, 255))
            dpg.add_file_extension(".*")

        with dpg.window(
            tag="error_popup",
            label="Error",
            modal=True,
            show=False,
            no_resize=True,
            no_collapse=True,
            width=720,
            height=280,
        ):
            dpg.add_text("Retrosync hit an error.")
            dpg.add_separator()
            dpg.add_text(tag="error_popup_message", wrap=680, default_value="")
            dpg.add_spacer(height=12)
            dpg.add_button(
                label="Close",
                width=self.ACTION_BUTTON_WIDTH,
                callback=lambda: dpg.configure_item("error_popup", show=False),
            )

        with dpg.window(
            tag="main_window",
            label="Retrosync",
            no_title_bar=True,
            no_collapse=True,
            no_close=True,
            no_move=True,
            no_resize=True,
        ):
            with dpg.group(horizontal=True):
                with dpg.child_window(tag="sidebar", width=330, height=-1, border=True):
                    dpg.add_text("Run Setup")
                    dpg.add_separator()
                    dpg.add_text("Config")
                    with dpg.group(horizontal=True):
                        dpg.add_button(
                            label="Load Config",
                            callback=lambda: dpg.show_item("config_file_dialog"),
                            width=self.ACTION_BUTTON_WIDTH,
                        )
                    dpg.add_spacer(height=10)
                    dpg.add_text("Actions")
                    for field_name, label in [
                        ("do_sync_playlists", "Sync playlists"),
                        ("do_sync_roms", "Sync ROMs"),
                        ("do_sync_bios", "Sync BIOS"),
                        ("do_sync_favorites", "Sync favorites"),
                        ("do_sync_shaders", "Sync shaders"),
                        ("do_sync_thumbnails", "Sync thumbnails"),
                        ("do_update_playlists", "Update playlists"),
                        ("do_update_thumbnails", "Update thumbnails"),
                        ("apply_changes", "Apply thumbnail changes"),
                        ("do_debug", "Debug logging"),
                    ]:
                        dpg.add_checkbox(
                            tag=f"action::{field_name}",
                            label=label,
                            callback=self._toggle_action,
                            user_data=field_name,
                        )
                    with dpg.collapsing_header(label="Advanced Options", default_open=False):
                        for field_name, label in [
                            ("refresh_thumbnail_cache", "Refresh thumbnail cache"),
                            ("no_thumbnail_cache", "Disable thumbnail cache"),
                        ]:
                            dpg.add_checkbox(
                                tag=f"action::{field_name}",
                                label=label,
                                callback=self._toggle_action,
                                user_data=field_name,
                            )
                        dpg.add_combo(
                            tag="force_transport",
                            items=["auto", "unix", "windows"],
                            default_value="auto",
                            width=-1,
                            callback=self._set_force_transport,
                        )

                with dpg.child_window(tag="content", width=-1, height=-1, border=False):
                    dpg.add_text(tag="status_line", default_value="Load a config to begin.")
                    dpg.add_separator()
                    with dpg.tab_bar():
                        with dpg.tab(label="Run"):
                            dpg.add_text(tag="summary_config")
                            dpg.add_text(tag="summary_transport")
                            dpg.add_text(tag="summary_systems")
                            dpg.add_text(tag="summary_disabled_systems")
                            dpg.add_text(tag="summary_actions")
                            dpg.add_text(tag="summary_state")
                            dpg.add_text(tag="summary_transport_status")
                            dpg.add_text(tag="summary_preview")
                            dpg.add_progress_bar(tag="run_progress", default_value=0.0, width=-1)
                            dpg.add_spacer(height=12)
                            with dpg.group(horizontal=True):
                                dpg.add_button(
                                    tag="run_button",
                                    label="Run",
                                    width=self.ACTION_BUTTON_WIDTH,
                                    callback=lambda: self._handle_run_click(dry_run=False),
                                )
                                dpg.add_button(
                                    tag="dry_run_button",
                                    label="Dry Run",
                                    width=self.ACTION_BUTTON_WIDTH,
                                    callback=lambda: self._handle_run_click(dry_run=True),
                                )
                                dpg.add_button(
                                    tag="cancel_button",
                                    label="Cancel",
                                    width=self.ACTION_BUTTON_WIDTH,
                                    callback=self._handle_cancel_click,
                                )
                            dpg.add_spacer(height=12)
                            dpg.add_separator()
                            dpg.add_text("Systems")
                            with dpg.group(horizontal=True):
                                dpg.add_button(
                                    label="Select All",
                                    width=self.ACTION_BUTTON_WIDTH,
                                    callback=lambda: self.controller.set_all_systems_selected(True),
                                )
                                dpg.add_button(
                                    label="Clear All",
                                    width=self.ACTION_BUTTON_WIDTH,
                                    callback=lambda: self.controller.set_all_systems_selected(
                                        False
                                    ),
                                )
                                dpg.add_input_text(
                                    tag="system_filter",
                                    hint="Filter systems",
                                    width=240,
                                    callback=lambda _s, app_data: self.controller.set_system_filter(
                                        app_data
                                    ),
                                )
                                dpg.add_button(
                                    tag="select_filtered_button",
                                    label="Select Filtered",
                                    width=self.ACTION_BUTTON_WIDTH,
                                    callback=lambda: self._select_filtered_systems(),
                                )
                            with dpg.child_window(
                                tag="systems_container", autosize_x=True, autosize_y=True
                            ):
                                dpg.add_table(
                                    tag="systems_table",
                                    header_row=True,
                                    row_background=True,
                                    resizable=True,
                                    policy=dpg.mvTable_SizingStretchProp,
                                )
                                dpg.add_table_column(
                                    parent="systems_table",
                                    label="Use",
                                    init_width_or_weight=0.7,
                                )
                                dpg.add_table_column(
                                    parent="systems_table",
                                    label="System",
                                    init_width_or_weight=2.2,
                                )
                                dpg.add_table_column(
                                    parent="systems_table",
                                    label="Source",
                                    init_width_or_weight=1.4,
                                )
                                dpg.add_table_column(
                                    parent="systems_table",
                                    label="Destination",
                                    init_width_or_weight=1.4,
                                )
                                dpg.add_table_column(
                                    parent="systems_table",
                                    label="Status",
                                    init_width_or_weight=1.1,
                                )
                        with dpg.tab(label="Preview"):
                            dpg.add_text(tag="preview_header", default_value="No preview data yet.")
                            dpg.add_text(tag="preview_counts", default_value="")
                            with dpg.child_window(
                                tag="preview_plan_container",
                                height=-1,
                                autosize_x=True,
                                border=True,
                            ):
                                dpg.add_table(
                                    tag="preview_plan_table",
                                    header_row=True,
                                    row_background=True,
                                    resizable=True,
                                    policy=dpg.mvTable_SizingStretchProp,
                                )
                                for label, weight in [
                                    ("Action", 1.0),
                                    ("Op", 0.8),
                                    ("System", 1.0),
                                    ("Source", 2.5),
                                    ("Destination", 2.5),
                                    ("Size", 0.8),
                                ]:
                                    dpg.add_table_column(
                                        parent="preview_plan_table",
                                        label=label,
                                        init_width_or_weight=weight,
                                    )
                    dpg.add_separator()
                    dpg.add_text(tag="error_line", default_value="")

    def _autoload_initial_config(self):
        config_path = self.controller.snapshot().config_path.strip()
        if not config_path:
            return
        if Path(config_path).is_file():
            self.controller.load_config(config_path)

    def _toggle_action(self, _sender, app_data, user_data):
        self.controller.set_action(user_data, app_data)

    def _handle_run_click(self, *, dry_run):
        state = self.controller.snapshot()
        if not self._can_start_run(state):
            return
        self.controller.start_run(dry_run=dry_run)

    def _handle_cancel_click(self):
        state = self.controller.snapshot()
        if not self._can_cancel_run(state):
            return
        self.controller.cancel_run()

    def _select_filtered_systems(self):
        self.controller.set_system_filter(dpg.get_value("system_filter"))
        self.controller.set_filtered_systems_selected(True)

    def _load_selected_config(self, _sender, app_data):
        path = app_data.get("file_path_name", "")
        if not path:
            return
        self.controller.load_config(path)

    def _on_viewport_resize(self, _sender=None, _app_data=None):
        self._sync_main_window_to_viewport()

    def _sync_main_window_to_viewport(self):
        dpg.configure_item(
            "main_window",
            pos=(0, 0),
            width=max(1, dpg.get_viewport_client_width()),
            height=max(1, dpg.get_viewport_client_height()),
        )

    def _set_force_transport(self, _sender, app_data):
        value = False if app_data == "auto" else app_data
        self.controller.set_action("force_transport", value)

    def _refresh(self, *, force=False):
        state = self.controller.snapshot()
        dpg.set_value("system_filter", state.system_filter)
        dpg.set_value(
            "force_transport",
            state.run_setup.force_transport if state.run_setup.force_transport else "auto",
        )
        for field_name in [
            "do_sync_playlists",
            "do_sync_roms",
            "do_sync_bios",
            "do_sync_favorites",
            "do_sync_shaders",
            "do_sync_thumbnails",
            "do_update_playlists",
            "do_update_thumbnails",
            "apply_changes",
            "do_debug",
            "refresh_thumbnail_cache",
            "no_thumbnail_cache",
        ]:
            dpg.set_value(f"action::{field_name}", getattr(state.run_setup, field_name))

        selected_count = len([row for row in state.systems if row.selected and not row.disabled])
        disabled_count = len([row for row in state.systems if row.disabled])
        selected_actions = [
            label
            for field_name, label in [
                ("do_sync_playlists", "playlists"),
                ("do_sync_roms", "ROMs"),
                ("do_sync_bios", "BIOS"),
                ("do_sync_favorites", "favorites"),
                ("do_sync_shaders", "shaders"),
                ("do_sync_thumbnails", "thumbnails"),
                ("do_update_playlists", "update playlists"),
                ("do_update_thumbnails", "update thumbnails"),
            ]
            if getattr(state.run_setup, field_name)
        ]
        dpg.set_value("status_line", state.status_message)
        dpg.set_value("summary_config", f"Config: {state.config_path or 'None'}")
        dpg.set_value("summary_transport", f"Transport: {state.transport or 'n/a'}")
        dpg.set_value(
            "summary_systems", f"Selected systems: {selected_count} / {len(state.systems)}"
        )
        dpg.set_value(
            "summary_disabled_systems",
            f"Disabled systems: {disabled_count}" if disabled_count else "Disabled systems: none",
        )
        dpg.set_value(
            "summary_actions",
            f"Actions: {', '.join(selected_actions) if selected_actions else 'none'}",
        )
        dpg.set_value(
            "summary_state",
            f"State: {state.run_state.status} | Detail: {state.run_state.active_detail or 'idle'}",
        )
        transport_text = (
            f"Transport status: {state.transport_status.severity} | {state.transport_status.message}"
            if state.transport_status.visible
            else "Transport status: idle"
        )
        dpg.set_value("summary_transport_status", transport_text)
        dpg.set_value(
            "summary_preview",
            "Plan: "
            f"{len(state.preview.plan_rows)} row(s) | "
            f"Copies {state.preview.planned_copies} | "
            f"Skips {state.preview.planned_skips} | "
            f"Overwrites {state.preview.planned_overwrites} | "
            f"Rewrites {state.preview.planned_rewrites} | "
            f"Downloads {state.preview.planned_downloads} | "
            f"Estimated transfer {self._format_bytes(state.preview.estimated_transfer_bytes)}",
        )
        progress_total = state.run_state.progress_total or 0
        progress_value = (
            min(state.run_state.progress_current / progress_total, 1.0) if progress_total else 0.0
        )
        dpg.set_value("run_progress", progress_value)
        run_controls_enabled = self._can_start_run(state)
        self._set_button_enabled_visual("run_button", run_controls_enabled)
        self._set_button_enabled_visual("dry_run_button", run_controls_enabled)
        cancel_enabled = self._can_cancel_run(state)
        dpg.bind_item_theme(
            "cancel_button",
            self._cancel_button_theme if cancel_enabled else self._cancel_disabled_button_theme,
        )
        dpg.set_value("error_line", state.run_state.last_error or state.config_error or "")
        self._refresh_error_popup(state)

        filter_text = state.system_filter.strip().lower()
        matching_rows = [
            row for row in state.systems if filter_text and filter_text in row.name.lower()
        ]
        filtered_action_enabled = bool(state.system_filter.strip()) and bool(matching_rows)
        dpg.configure_item("select_filtered_button", enabled=filtered_action_enabled)
        system_signature = tuple(
            (
                row.playlist_name,
                row.selected,
                row.status,
                row.last_message,
                row.disabled,
                bool(filter_text and filter_text in row.name.lower()),
            )
            for row in state.systems
        )
        if force or system_signature != self._last_system_signature:
            dpg.delete_item("systems_table", children_only=True, slot=1)
            for row in state.systems:
                is_match = bool(filter_text and filter_text in row.name.lower())
                with dpg.table_row(parent="systems_table"):
                    dpg.add_checkbox(
                        default_value=row.selected,
                        enabled=not row.disabled,
                        callback=self._toggle_system,
                        user_data=row.playlist_name,
                    )
                    dpg.add_text(
                        f"{row.name} [disabled]" if row.disabled else row.name,
                        color=(
                            (130, 138, 144)
                            if row.disabled
                            else self.MATCH_TEXT_COLOR
                            if is_match
                            else None
                        ),
                    )
                    dpg.add_text(
                        row.source_folder or "",
                        color=(
                            (130, 138, 144)
                            if row.disabled
                            else self.MATCH_TEXT_COLOR
                            if is_match
                            else None
                        ),
                    )
                    dpg.add_text(
                        row.destination_folder or "",
                        color=(
                            (130, 138, 144)
                            if row.disabled
                            else self.MATCH_TEXT_COLOR
                            if is_match
                            else None
                        ),
                    )
                    if row.disabled:
                        status_text = "disabled in config"
                    else:
                        status_text = (
                            row.status
                            if not row.last_message
                            else f"{row.status}: {row.last_message}"
                        )
                    dpg.add_text(
                        status_text,
                        color=(
                            (130, 138, 144)
                            if row.disabled
                            else self.MATCH_TEXT_COLOR
                            if is_match
                            else None
                        ),
                    )
            self._last_system_signature = system_signature

        plan_signature = tuple(
            (
                row.action,
                row.operation,
                row.system,
                row.source,
                row.destination,
                row.size_bytes,
                row.details,
            )
            for row in state.preview.plan_rows[:500]
        )
        if force or self._last_plan_signature != plan_signature:
            dpg.delete_item("preview_plan_table", children_only=True, slot=1)
            for row in state.preview.plan_rows[:500]:
                with dpg.table_row(parent="preview_plan_table"):
                    dpg.add_text(row.action)
                    dpg.add_text(row.operation)
                    dpg.add_text(row.system)
                    dpg.add_text(row.source)
                    dpg.add_text(row.destination)
                    dpg.add_text(str(row.size_bytes))
            dpg.set_value(
                "preview_counts",
                "Copies: "
                f"{state.preview.planned_copies} | "
                f"Skips: {state.preview.planned_skips} | "
                f"Overwrites: {state.preview.planned_overwrites} | "
                f"Rewrites: {state.preview.planned_rewrites} | "
                f"Downloads: {state.preview.planned_downloads}",
            )
            self._last_plan_signature = plan_signature
        if not state.preview.plan_rows:
            dpg.set_value("preview_header", "No preview data yet.")
        else:
            dpg.set_value(
                "preview_header",
                f"{len(state.preview.plan_rows)} plan row(s) | "
                f"Estimated transfer {self._format_bytes(state.preview.estimated_transfer_bytes)}",
            )

    def _toggle_system(self, _sender, app_data, user_data):
        self.controller.set_system_selected(user_data, app_data)

    def _format_bytes(self, size_bytes):
        if size_bytes >= 1024**3:
            return f"{size_bytes / (1024**3):.2f} GB"
        if size_bytes >= 1024**2:
            return f"{size_bytes / (1024**2):.2f} MB"
        if size_bytes >= 1024:
            return f"{size_bytes / 1024:.1f} KB"
        return f"{size_bytes} B"

    def _set_button_enabled_visual(self, tag, enabled):
        dpg.bind_item_theme(
            tag,
            self._action_button_theme if enabled else self._disabled_button_theme,
        )

    def _can_start_run(self, state):
        if not state.config_loaded or state.config_error:
            return False
        selected_count = len([row for row in state.systems if row.selected and not row.disabled])
        action_selected = any(
            getattr(state.run_setup, field_name)
            for field_name in [
                "do_sync_playlists",
                "do_sync_roms",
                "do_sync_bios",
                "do_sync_favorites",
                "do_sync_shaders",
                "do_sync_thumbnails",
                "do_update_playlists",
                "do_update_thumbnails",
            ]
        )
        return selected_count > 0 and action_selected and state.run_state.status != "running"

    def _can_cancel_run(self, state):
        return state.run_state.can_cancel and state.run_state.status in {"validating", "running"}

    def _refresh_error_popup(self, state):
        message = state.config_error or ""
        signature = state.config_error
        if message and signature != self._last_error_signature:
            dpg.set_value("error_popup_message", message)
            dpg.configure_item("error_popup", show=True)
        self._last_error_signature = signature


def launch_gui():
    RetrosyncGuiApp().run()
