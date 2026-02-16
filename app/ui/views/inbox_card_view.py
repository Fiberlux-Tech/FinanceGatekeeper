"""
Inbox Card View — Master-Detail Split Panel.

The primary "Gatekeeper" module view.  Replaces the Phase 1
``DashboardView`` placeholder with a live, file-aware master-detail
split panel:

- **Left panel** — scrollable list of ``FileCard`` widgets
  (one per inbox file, showing client, MRC, salesman, date).
- **Right panel** — ``DetailPanel`` showing full info for the
  selected card (financial summary, chain of custody, metadata,
  action buttons).

All file I/O (scanning, hashing, metadata extraction) runs on
worker threads.  Watchdog events are marshalled to the UI thread
via ``self.after()``.

**Thin UI Rule**: Zero business logic — delegates everything to
``InboxScanService``, ``NativeOpenerService``, and the
``FileWatcherService`` callback mechanism.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Optional

import customtkinter as ctk

from app.auth import SessionManager
from app.logger import StructuredLogger
from app.models.card_models import CardData
from app.models.enums import FileEventType
from app.models.file_models import FileEvent
from app.services.file_watcher import FileWatcherService
from app.services.inbox_scan_service import InboxScanService
from app.services.native_opener import NativeOpenerService
from app.services.excel_parser import ExcelParserService
from app.services.transaction_crud import TransactionCrudService
from app.services.transaction_workflow import TransactionWorkflowService
from app.ui.components.detail_panel import DetailPanel
from app.ui.components.file_card import FileCard
from app.ui.theme import (
    ACCENT_HOVER,
    ACCENT_PRIMARY,
    CONTENT_BG,
    CORNER_RADIUS,
    FONT_BODY,
    FONT_BUTTON,
    FONT_HEADING,
    PADDING_LG,
    PADDING_MD,
    PADDING_SM,
    TEXT_LIGHT,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
)

_DEBOUNCE_MS: int = 500  # Coalesce rapid watchdog events per path


class InboxCardView(ctk.CTkFrame):
    """Master-detail inbox view — the "Gatekeeper" module.

    Parameters
    ----------
    parent:
        Content container provided by the Host Shell.
    session:
        Current user session (for display purposes).
    inbox_scan:
        ``InboxScanService`` for inbox scanning.  ``None`` when the
        SharePoint path has not been configured.
    file_watcher:
        ``FileWatcherService`` for real-time file events.  ``None``
        when the SharePoint path has not been configured.
    native_opener:
        ``NativeOpenerService`` for opening files/folders.
    transaction_workflow:
        Approval/rejection orchestration with file archival.
    transaction_crud:
        Transaction creation and data persistence.
    excel_parser:
        Full Excel file parsing for transaction creation.
    logger:
        Structured logger instance.
    """

    def __init__(
        self,
        parent: ctk.CTkFrame,
        session: SessionManager,
        inbox_scan: Optional[InboxScanService],
        file_watcher: Optional[FileWatcherService],
        native_opener: NativeOpenerService,
        transaction_workflow: Optional[TransactionWorkflowService],
        transaction_crud: Optional[TransactionCrudService],
        excel_parser: Optional[ExcelParserService],
        logger: StructuredLogger,
    ) -> None:
        super().__init__(parent, fg_color=CONTENT_BG)
        self._session = session
        self._inbox_scan = inbox_scan
        self._file_watcher = file_watcher
        self._native_opener = native_opener
        self._transaction_workflow = transaction_workflow
        self._transaction_crud = transaction_crud
        self._excel_parser = excel_parser
        self._logger = logger

        # Card state
        self._cards: dict[Path, FileCard] = {}
        self._selected_path: Optional[Path] = None

        # Debounce timers for watchdog events (path → after-id)
        self._debounce_timers: dict[Path, str] = {}

        # Pending after() job IDs for cleanup on destroy
        self._pending_jobs: list[str] = []

        self._build_ui()

        # Register watchdog callback + trigger initial scan
        if self._file_watcher is not None:
            self._file_watcher.set_callback(self._on_file_event)

        if self._inbox_scan is not None:
            self._trigger_full_scan()

    # ==================================================================
    # Widget construction
    # ==================================================================

    def _build_ui(self) -> None:
        """Create the header and master-detail split layout."""
        # --- Header ---
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=PADDING_LG, pady=(PADDING_LG, PADDING_SM))

        ctk.CTkLabel(
            header, text="Inbox",
            font=FONT_HEADING, text_color=TEXT_PRIMARY, anchor="w",
        ).pack(side="left")

        self._refresh_btn = ctk.CTkButton(
            header,
            text="\u21BB  Refresh",
            font=FONT_BUTTON,
            fg_color="transparent",
            hover_color=ACCENT_HOVER,
            text_color=ACCENT_PRIMARY,
            border_width=1,
            border_color=ACCENT_PRIMARY,
            width=110,
            corner_radius=CORNER_RADIUS,
            command=self._on_refresh_all,
        )
        self._refresh_btn.pack(side="right")

        # --- Split container ---
        split = ctk.CTkFrame(self, fg_color="transparent")
        split.pack(fill="both", expand=True, padx=PADDING_LG, pady=(0, PADDING_LG))

        # Left panel — scrollable card list
        left = ctk.CTkFrame(split, fg_color="transparent")
        left.pack(side="left", fill="both")

        self._card_list = ctk.CTkScrollableFrame(
            left, fg_color=CONTENT_BG, width=300,
        )
        self._card_list.pack(fill="both", expand=True)

        # Right panel — detail view
        right = ctk.CTkFrame(split, fg_color="transparent")
        right.pack(side="left", fill="both", expand=True, padx=(PADDING_MD, 0))

        self._detail_panel = DetailPanel(
            parent=right,
            on_open_file=self._on_open_file,
            on_open_folder=self._on_open_folder,
            on_refresh=self._on_refresh_single,
            on_approve=self._on_approve,
            on_reject=self._on_reject,
        )
        self._detail_panel.pack(fill="both", expand=True)

        # Empty state overlay (shown when no scan service)
        if self._inbox_scan is None:
            self._show_no_watcher_state()

    def _show_no_watcher_state(self) -> None:
        """Show message when no SharePoint path is configured."""
        ctk.CTkLabel(
            self._card_list,
            text=(
                "SharePoint folder not configured.\n"
                "Go to Settings to set your folder path."
            ),
            font=FONT_BODY,
            text_color=TEXT_SECONDARY,
            justify="center",
        ).pack(pady=PADDING_LG)

    def _show_empty_inbox(self) -> None:
        """Show empty state when inbox has no files."""
        for widget in self._card_list.winfo_children():
            widget.destroy()
        self._cards.clear()

        ctk.CTkLabel(
            self._card_list,
            text="No files in inbox.\nWaiting for incoming sales sheets...",
            font=FONT_BODY,
            text_color=TEXT_SECONDARY,
            justify="center",
        ).pack(pady=PADDING_LG)

    # ==================================================================
    # Full scan (initial load + refresh all)
    # ==================================================================

    def _trigger_full_scan(self) -> None:
        """Spawn a worker thread to scan the entire inbox."""
        if self._inbox_scan is None:
            return

        self._refresh_btn.configure(state="disabled", text="\u21BB  Scanning...")

        scan_service = self._inbox_scan

        def _worker() -> None:
            cards = scan_service.scan_inbox()
            job = self.after(0, self._populate_cards, cards)
            self._pending_jobs.append(job)

        thread = threading.Thread(
            target=_worker, name="inbox-scan", daemon=True,
        )
        thread.start()

    def _populate_cards(self, cards: list[CardData]) -> None:
        """Rebuild the entire card list from scan results.

        Called on the UI thread via ``self.after()``.
        """
        if not self.winfo_exists():
            return

        # Clear existing cards
        for widget in self._card_list.winfo_children():
            widget.destroy()
        self._cards.clear()

        if not cards:
            self._show_empty_inbox()
            self._refresh_btn.configure(state="normal", text="\u21BB  Refresh")
            return

        previously_selected = self._selected_path

        for data in cards:
            card = FileCard(
                parent=self._card_list,
                card_data=data,
                on_select=self._on_card_selected,
            )
            card.pack(fill="x", pady=(0, PADDING_SM))
            self._cards[data.path] = card

        # Restore selection if the previously selected file still exists
        if previously_selected and previously_selected in self._cards:
            self._select_card(previously_selected)
        else:
            self._selected_path = None
            self._detail_panel.show_empty()

        self._refresh_btn.configure(state="normal", text="\u21BB  Refresh")

    # ==================================================================
    # Card selection
    # ==================================================================

    def _on_card_selected(self, path: Path) -> None:
        """Handle card click — select and show detail."""
        self._select_card(path)

    def _select_card(self, path: Path) -> None:
        """Visually select a card and populate the detail panel."""
        # Deselect previous
        if self._selected_path and self._selected_path in self._cards:
            self._cards[self._selected_path].set_selected(False)

        self._selected_path = path

        if path in self._cards:
            card = self._cards[path]
            card.set_selected(True)
            self._detail_panel.show_card(card.card_data)

    # ==================================================================
    # Watchdog event handling
    # ==================================================================

    def _on_file_event(self, event: FileEvent) -> None:
        """Callback invoked from the watchdog thread.

        Marshals processing to the UI thread via ``self.after()``.
        """
        job = self.after(0, self._handle_file_event, event)
        self._pending_jobs.append(job)

    def _handle_file_event(self, event: FileEvent) -> None:
        """Process a file event on the UI thread.

        Debounces rapid MODIFIED events per path.
        """
        if not self.winfo_exists():
            return

        path = event.file.path

        if event.event_type == FileEventType.DELETED:
            self._remove_card(path)
            return

        # Debounce CREATED / MODIFIED — cancel previous timer for this path
        if path in self._debounce_timers:
            self.after_cancel(self._debounce_timers[path])

        timer_id = self.after(
            _DEBOUNCE_MS,
            self._scan_and_upsert_card,
            path,
        )
        self._debounce_timers[path] = timer_id

    def _scan_and_upsert_card(self, path: Path) -> None:
        """Scan a single file on a worker thread and upsert the card."""
        self._debounce_timers.pop(path, None)

        if self._inbox_scan is None:
            return

        scan_service = self._inbox_scan

        def _worker() -> None:
            card_data = scan_service.scan_single_file(path)
            job = self.after(0, self._upsert_card, card_data)
            self._pending_jobs.append(job)

        thread = threading.Thread(
            target=_worker, name=f"scan-{path.name}", daemon=True,
        )
        thread.start()

    def _upsert_card(self, data: CardData) -> None:
        """Insert or update a card in the master list."""
        if not self.winfo_exists():
            return

        path = data.path

        if path in self._cards:
            # Update existing card
            self._cards[path].update_data(data)
            # If this card is selected, refresh the detail panel too
            if self._selected_path == path:
                self._detail_panel.show_card(data)
        else:
            # New card — insert at the top of the list
            card = FileCard(
                parent=self._card_list,
                card_data=data,
                on_select=self._on_card_selected,
            )
            # Pack at the beginning by reordering
            card.pack(fill="x", pady=(0, PADDING_SM))
            self._cards[path] = card

            # Card count updated implicitly by pack presence

    def _remove_card(self, path: Path) -> None:
        """Remove a card from the master list (file deleted)."""
        if path in self._cards:
            self._cards[path].destroy()
            del self._cards[path]

        if self._selected_path == path:
            self._selected_path = None
            self._detail_panel.show_empty()

        if not self._cards:
            self._show_empty_inbox()

    # ==================================================================
    # Action callbacks
    # ==================================================================

    def _on_open_file(self, path: Path) -> None:
        """Open the file in Excel via NativeOpenerService."""
        self._native_opener.open_file(path)

    def _on_open_folder(self, path: Path) -> None:
        """Open the containing folder via NativeOpenerService."""
        self._native_opener.open_folder(path)

    def _on_refresh_single(self, path: Path) -> None:
        """Re-scan a single file from the detail panel Refresh button."""
        self._scan_and_upsert_card(path)

    def _on_refresh_all(self) -> None:
        """Re-scan the entire inbox from the header Refresh All button."""
        self._trigger_full_scan()

    # ==================================================================
    # Approve / Reject workflows
    # ==================================================================

    def _on_approve(self, card_data: CardData) -> None:
        """Handle approve button — full-parse, create transaction, archive.

        All I/O runs on a worker thread.  The result is marshalled back
        to the UI thread via ``self.after()``.
        """
        if self._transaction_workflow is None or self._transaction_crud is None or self._excel_parser is None:
            self._show_error_dialog("Approval Error", "Required services are not available.")
            return

        current_user = self._session.get_current_user()
        workflow = self._transaction_workflow
        crud = self._transaction_crud
        parser = self._excel_parser

        def _worker() -> None:
            try:
                # Step 1: Full-parse the Excel file
                parse_result = parser.process_local_file(card_data.path)
                if not parse_result.success:
                    self.after(0, self._show_error_dialog, "Approval Error", f"Excel parse failed: {parse_result.error}")
                    return

                # Step 2: Create the transaction in the database
                save_result = crud.save_transaction(parse_result.data, current_user)
                if not save_result.success:
                    self.after(0, self._show_error_dialog, "Approval Error", f"Transaction creation failed: {save_result.error}")
                    return

                transaction_id: str = save_result.data["transaction_id"]

                # Step 3: Approve with file archival
                approve_result = workflow.approve_transaction_with_archival(
                    transaction_id=transaction_id,
                    current_user=current_user,
                    source_file_path=card_data.path,
                    business_unit=card_data.business_unit,
                    expected_sha256=card_data.sha256 or "",
                )

                if approve_result.success:
                    self.after(0, self._handle_approval_success, card_data.path)
                else:
                    self.after(0, self._show_error_dialog, "Approval Error", approve_result.error or "Unknown error")
            except Exception as exc:
                self.after(0, self._show_error_dialog, "Approval Error", str(exc))

        thread = threading.Thread(target=_worker, name="approve-tx", daemon=True)
        thread.start()

    def _on_reject(self, card_data: CardData) -> None:
        """Handle reject button — prompt for note, then full-parse, create, archive.

        The rejection note dialog runs on the UI thread. The I/O runs
        on a worker thread after the user enters the note.
        """
        if self._transaction_workflow is None or self._transaction_crud is None or self._excel_parser is None:
            self._show_error_dialog("Rejection Error", "Required services are not available.")
            return

        # Prompt for rejection note on the UI thread
        dialog = ctk.CTkInputDialog(
            text="Enter rejection reason:",
            title="Reject Transaction",
        )
        rejection_note = dialog.get_input()

        if not rejection_note or not rejection_note.strip():
            return  # User cancelled or empty note

        rejection_note = rejection_note.strip()

        current_user = self._session.get_current_user()
        workflow = self._transaction_workflow
        crud = self._transaction_crud
        parser = self._excel_parser

        def _worker() -> None:
            try:
                # Step 1: Full-parse the Excel file
                parse_result = parser.process_local_file(card_data.path)
                if not parse_result.success:
                    self.after(0, self._show_error_dialog, "Rejection Error", f"Excel parse failed: {parse_result.error}")
                    return

                # Step 2: Create the transaction in the database
                save_result = crud.save_transaction(parse_result.data, current_user)
                if not save_result.success:
                    self.after(0, self._show_error_dialog, "Rejection Error", f"Transaction creation failed: {save_result.error}")
                    return

                transaction_id: str = save_result.data["transaction_id"]

                # Step 3: Reject with file archival
                reject_result = workflow.reject_transaction_with_archival(
                    transaction_id=transaction_id,
                    current_user=current_user,
                    rejection_note=rejection_note,
                    source_file_path=card_data.path,
                    business_unit=card_data.business_unit,
                    expected_sha256=card_data.sha256 or "",
                )

                if reject_result.success:
                    self.after(0, self._handle_rejection_success, card_data.path)
                else:
                    self.after(0, self._show_error_dialog, "Rejection Error", reject_result.error or "Unknown error")
            except Exception as exc:
                self.after(0, self._show_error_dialog, "Rejection Error", str(exc))

        thread = threading.Thread(target=_worker, name="reject-tx", daemon=True)
        thread.start()

    def _handle_approval_success(self, path: Path) -> None:
        """Remove the card after successful approval (file moved out of inbox)."""
        if not self.winfo_exists():
            return
        self._remove_card(path)
        self._logger.info("File approved and archived: %s", path.name)

    def _handle_rejection_success(self, path: Path) -> None:
        """Remove the card after successful rejection (file moved out of inbox)."""
        if not self.winfo_exists():
            return
        self._remove_card(path)
        self._logger.info("File rejected and archived: %s", path.name)

    def _show_error_dialog(self, title: str, message: str) -> None:
        """Show an error dialog on the UI thread."""
        if not self.winfo_exists():
            return
        self._logger.error("%s: %s", title, message)

        dialog = ctk.CTkToplevel(self)
        dialog.title(title)
        dialog.geometry("450x200")
        dialog.resizable(False, False)
        dialog.transient(self.winfo_toplevel())
        dialog.grab_set()

        ctk.CTkLabel(
            dialog,
            text=message,
            font=FONT_BODY,
            text_color=TEXT_PRIMARY,
            wraplength=400,
        ).pack(padx=PADDING_MD, pady=(PADDING_LG, PADDING_SM))

        ctk.CTkButton(
            dialog,
            text="OK",
            font=FONT_BUTTON,
            fg_color=ACCENT_PRIMARY,
            hover_color=ACCENT_HOVER,
            text_color=TEXT_LIGHT,
            command=dialog.destroy,
        ).pack(pady=(0, PADDING_MD))

    # ==================================================================
    # Lifecycle
    # ==================================================================

    def destroy(self) -> None:
        """Cancel all pending timers and unregister the watcher callback."""
        # Cancel debounce timers
        for timer_id in self._debounce_timers.values():
            try:
                self.after_cancel(timer_id)
            except ValueError:
                pass
        self._debounce_timers.clear()

        # Cancel pending after() jobs
        for job in self._pending_jobs:
            try:
                self.after_cancel(job)
            except ValueError:
                pass
        self._pending_jobs.clear()

        # Unregister watchdog callback to prevent calls on dead widget
        if self._file_watcher is not None:
            self._file_watcher.set_callback(lambda _: None)

        super().destroy()
