"""
Optimizer Tab - Tkinter UI for the Relic Build Optimizer.
"""
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog
import tkinter.font as tkfont
import pathlib

from globals import CHARACTER_NAMES, COLOR_MAP
from source_data_handler import SourceDataHandler
from build_optimizer import (
    BuildStore, BuildDefinition, BuildScorer, VesselOptimizer,
    RelicInventory, VesselResult, TIER_WEIGHTS,
)

RELIC_COLOR_HEX = {
    'Red': '#FF4444',
    'Blue': '#4488FF',
    'Yellow': '#B8860B',
    'Green': '#44BB44',
    'White': '#AAAAAA',
    None: '#888888',
}

TIER_DISPLAY = {
    "required": "Required Properties",
    "preferred": "Preferred Properties",
    "avoid": "Avoid Properties",
    "blacklist": "Blacklist Properties",
}

TIER_COLORS = {
    "required": "#FF4444",
    "preferred": "#4488FF",
    "avoid": "#888888",
    "blacklist": "#FF8C00",
}


def autosize_treeview_columns(tree, padding=20, min_width=50):
    for col in tree['columns']:
        header_text = tree.heading(col)['text']
        header_font = tkfont.nametofont('TkHeadingFont')
        max_content_width = header_font.measure(header_text)
        default_font = tkfont.nametofont('TkDefaultFont')
        children = tree.get_children()[:100]
        for item in children:
            values = tree.item(item, 'values')
            col_idx = tree['columns'].index(col)
            if col_idx < len(values):
                text = str(values[col_idx])
                text_width = default_font.measure(text)
                if text_width > max_content_width:
                    max_content_width = text_width
        final_width = max(min_width, max_content_width + padding)
        tree.column(col, width=final_width, minwidth=final_width, stretch=False)


class EffectSearchDialog:
    """Dialog for searching and selecting effects to add to a tier."""

    def __init__(self, parent, effects_list: list, character: str,
                 exclude_ids: set, show_debuffs_first: bool = False):
        self.result = None
        self.effects_list = effects_list
        self.character = character
        self.exclude_ids = exclude_ids

        self.dialog = tk.Toplevel(parent)
        self.dialog.title("Add Effect")
        self.dialog.geometry("500x450")
        self.dialog.transient(parent)
        self.dialog.grab_set()

        # Search
        search_frame = ttk.Frame(self.dialog)
        search_frame.pack(fill='x', padx=10, pady=10)
        ttk.Label(search_frame, text="Search:").pack(side='left', padx=5)
        self.search_var = tk.StringVar()
        self.search_var.trace('w', lambda *args: self._filter())
        entry = ttk.Entry(search_frame, textvariable=self.search_var)
        entry.pack(side='left', fill='x', expand=True, padx=5)
        entry.focus()

        # Character filter checkbox
        self.char_filter_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            search_frame, text=f"Only {character}",
            variable=self.char_filter_var,
            command=self._filter
        ).pack(side='left', padx=5)

        # Listbox
        list_frame = ttk.Frame(self.dialog)
        list_frame.pack(fill='both', expand=True, padx=10, pady=5)
        scrollbar = ttk.Scrollbar(list_frame)
        scrollbar.pack(side='right', fill='y')
        self.listbox = tk.Listbox(list_frame, yscrollcommand=scrollbar.set)
        self.listbox.pack(side='left', fill='both', expand=True)
        scrollbar.config(command=self.listbox.yview)
        self.listbox.bind('<Double-Button-1>', lambda e: self._on_select())

        # Buttons
        btn_frame = ttk.Frame(self.dialog)
        btn_frame.pack(fill='x', padx=10, pady=10)
        ttk.Button(btn_frame, text="Select",
                   command=self._on_select).pack(side='right', padx=5)
        ttk.Button(btn_frame, text="Cancel",
                   command=self.dialog.destroy).pack(side='right', padx=5)

        # Build filtered list
        self._all_items = []
        for eff in effects_list:
            eff_id = eff["id"]
            if eff_id in exclude_ids:
                continue
            self._all_items.append(eff)

        # Sort: debuffs first if requested, then alphabetically
        if show_debuffs_first:
            self._all_items.sort(
                key=lambda e: (0 if e["is_debuff"] else 1, e["name"]))
        else:
            self._all_items.sort(key=lambda e: e["name"])

        self._filter()

    def _filter(self):
        self.listbox.delete(0, tk.END)
        search = self.search_var.get().lower()
        char_only = self.char_filter_var.get()
        self._filtered = []

        for eff in self._all_items:
            if search and search not in eff["name"].lower():
                continue
            if char_only and self.character in eff.get("allow_per_character", {}):
                if not eff["allow_per_character"].get(self.character, True):
                    continue

            prefix = "[Curse] " if eff["is_debuff"] else ""
            self.listbox.insert(tk.END, f"{prefix}{eff['name']}")
            self._filtered.append(eff)

    def _on_select(self):
        sel = self.listbox.curselection()
        if sel:
            self.result = self._filtered[sel[0]]
            self.dialog.destroy()


class OptimizerTab:
    """The Optimizer tab UI."""

    def __init__(self, parent: ttk.Frame, data_source: SourceDataHandler,
                 items_json: dict, effects_json: dict, base_dir: pathlib.Path):
        self.parent = parent
        self.data_source = data_source
        self.items_json = items_json
        self.effects_json = effects_json
        self.scorer = BuildScorer(data_source)
        self.optimizer = VesselOptimizer(data_source, self.scorer)
        self.store = BuildStore(base_dir)

        # Cached effects list for search dialog
        self._effects_list = None

        # Inventory (set when save is loaded)
        self.inventory: RelicInventory = None

        self._setup_ui()
        self._refresh_build_list()

    def _get_effects_list(self):
        if self._effects_list is None:
            self._effects_list = self.data_source.get_all_effects_list()
        return self._effects_list

    def _setup_ui(self):
        # Main paned layout
        main_pane = ttk.PanedWindow(self.parent, orient='horizontal')
        main_pane.pack(fill='both', expand=True, padx=5, pady=5)

        # === LEFT PANEL ===
        left_frame = ttk.Frame(main_pane)
        main_pane.add(left_frame, weight=2)

        # -- Build selector bar --
        selector_frame = ttk.LabelFrame(left_frame, text="Build")
        selector_frame.pack(fill='x', padx=5, pady=(5, 2))

        # Row 1: Build dropdown + buttons
        row1 = ttk.Frame(selector_frame)
        row1.pack(fill='x', padx=5, pady=2)
        ttk.Label(row1, text="Build:").pack(side='left', padx=(0, 5))
        self.build_var = tk.StringVar()
        self.build_combo = ttk.Combobox(row1, textvariable=self.build_var,
                                        state='readonly', width=25)
        self.build_combo.pack(side='left', padx=2)
        self.build_combo.bind('<<ComboboxSelected>>', self._on_build_selected)
        ttk.Button(row1, text="+ New", width=6,
                   command=self._new_build).pack(side='left', padx=2)
        ttk.Button(row1, text="Rename", width=6,
                   command=self._rename_build).pack(side='left', padx=2)
        ttk.Button(row1, text="Delete", width=6,
                   command=self._delete_build).pack(side='left', padx=2)

        # Row 2: Character + options
        row2 = ttk.Frame(selector_frame)
        row2.pack(fill='x', padx=5, pady=2)
        ttk.Label(row2, text="Character:").pack(side='left', padx=(0, 5))
        self.char_var = tk.StringVar()
        self.char_combo = ttk.Combobox(row2, textvariable=self.char_var,
                                       values=CHARACTER_NAMES[:10],
                                       state='readonly', width=12)
        self.char_combo.pack(side='left', padx=2)
        self.char_combo.bind('<<ComboboxSelected>>', self._on_character_changed)

        ttk.Separator(row2, orient='vertical').pack(
            side='left', padx=8, fill='y')

        self.deep_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(row2, text="Include Deep Slots",
                        variable=self.deep_var,
                        command=self._on_option_changed).pack(side='left', padx=2)

        # -- Tier sections (scrollable) --
        tier_canvas_frame = ttk.Frame(left_frame)
        tier_canvas_frame.pack(fill='both', expand=True, padx=5, pady=2)

        tier_canvas = tk.Canvas(tier_canvas_frame, highlightthickness=0)
        tier_scrollbar = ttk.Scrollbar(tier_canvas_frame, orient='vertical',
                                       command=tier_canvas.yview)
        self.tier_inner = ttk.Frame(tier_canvas)
        self.tier_inner.bind(
            '<Configure>',
            lambda e: tier_canvas.configure(
                scrollregion=tier_canvas.bbox('all'))
        )
        tier_canvas.create_window((0, 0), window=self.tier_inner, anchor='nw')
        tier_canvas.configure(yscrollcommand=tier_scrollbar.set)
        tier_canvas.pack(side='left', fill='both', expand=True)
        tier_scrollbar.pack(side='right', fill='y')

        # Mousewheel scrolling
        def _on_mousewheel(event):
            tier_canvas.yview_scroll(int(-1 * (event.delta / 120)), 'units')
        tier_canvas.bind_all('<MouseWheel>', _on_mousewheel)

        # Create tier sections
        self.tier_trees = {}
        for tier_key in ["required", "preferred", "avoid", "blacklist"]:
            self._create_tier_section(tier_key)

        # Optimize button
        btn_frame = ttk.Frame(left_frame)
        btn_frame.pack(fill='x', padx=5, pady=5)
        self.optimize_btn = ttk.Button(
            btn_frame, text="Optimize", command=self._run_optimize)
        self.optimize_btn.pack(fill='x', padx=5)
        self.status_label = ttk.Label(btn_frame, text="Load a save file to begin",
                                      foreground='gray')
        self.status_label.pack(padx=5, pady=2)

        # === RIGHT PANEL (Results) ===
        right_frame = ttk.Frame(main_pane)
        main_pane.add(right_frame, weight=3)

        results_label = ttk.Label(right_frame, text="Optimization Results",
                                  font=('TkDefaultFont', 11, 'bold'))
        results_label.pack(padx=5, pady=(5, 2), anchor='w')

        # Scrollable results area
        results_canvas_frame = ttk.Frame(right_frame)
        results_canvas_frame.pack(fill='both', expand=True, padx=5, pady=2)

        self.results_canvas = tk.Canvas(results_canvas_frame, highlightthickness=0)
        results_scrollbar = ttk.Scrollbar(
            results_canvas_frame, orient='vertical',
            command=self.results_canvas.yview)
        self.results_inner = ttk.Frame(self.results_canvas)
        self.results_inner.bind(
            '<Configure>',
            lambda e: self.results_canvas.configure(
                scrollregion=self.results_canvas.bbox('all'))
        )
        self.results_canvas.create_window(
            (0, 0), window=self.results_inner, anchor='nw')
        self.results_canvas.configure(yscrollcommand=results_scrollbar.set)
        self.results_canvas.pack(side='left', fill='both', expand=True)
        results_scrollbar.pack(side='right', fill='y')

        def _on_results_mousewheel(event):
            self.results_canvas.yview_scroll(
                int(-1 * (event.delta / 120)), 'units')
        self.results_canvas.bind('<MouseWheel>', _on_results_mousewheel)
        self.results_inner.bind('<MouseWheel>', _on_results_mousewheel)

        # Placeholder
        self.results_placeholder = ttk.Label(
            self.results_inner,
            text="Create a build and click Optimize to see results.",
            foreground='gray')
        self.results_placeholder.pack(padx=20, pady=40)

    def _create_tier_section(self, tier_key: str):
        """Create a labeled frame with a treeview for one tier."""
        display_name = TIER_DISPLAY[tier_key]
        # Only show points for scored tiers (preferred, avoid)
        if tier_key in ("preferred", "avoid"):
            weight_str = f" ({TIER_WEIGHTS[tier_key]:+d} pts)"
        elif tier_key == "required":
            weight_str = " (Absolute Requirement)"
        else:  # blacklist
            weight_str = " (Absolute Exclusion)"
        color = TIER_COLORS[tier_key]

        frame = ttk.LabelFrame(self.tier_inner, text=f"{display_name}{weight_str}")
        frame.pack(fill='x', padx=5, pady=3)

        # Treeview
        tree = ttk.Treeview(frame, columns=('effect',), show='headings',
                            height=3, selectmode='browse')
        tree.heading('effect', text='Effect Name')
        tree.column('effect', stretch=True)
        tree.pack(fill='x', padx=5, pady=(2, 0))

        # Tag for coloring
        tree.tag_configure('item', foreground=color)

        # Buttons
        btn_row = ttk.Frame(frame)
        btn_row.pack(fill='x', padx=5, pady=(2, 5))

        is_blacklist = tier_key == "blacklist"
        ttk.Button(btn_row, text="+ Add", width=8,
                   command=lambda t=tier_key: self._add_effect(t, is_blacklist)
                   ).pack(side='left', padx=2)
        ttk.Button(btn_row, text="Remove", width=8,
                   command=lambda t=tier_key: self._remove_effect(t)
                   ).pack(side='left', padx=2)

        self.tier_trees[tier_key] = tree

    # ---- Build Management ----

    def _refresh_build_list(self):
        builds = self.store.list_builds()
        names = [f"{b.name} ({b.character})" for b in builds]
        self.build_combo['values'] = names
        self._build_ids = [b.id for b in builds]
        if not builds:
            self.build_var.set('')
            self._clear_ui()

    def _get_current_build(self) -> BuildDefinition:
        idx = self.build_combo.current()
        if idx < 0 or idx >= len(self._build_ids):
            return None
        return self.store.get(self._build_ids[idx])

    def _on_build_selected(self, event=None):
        build = self._get_current_build()
        if build:
            self._load_build_into_ui(build)

    def _load_build_into_ui(self, build: BuildDefinition):
        """Populate UI from a build definition."""
        # Character
        if build.character in CHARACTER_NAMES[:10]:
            self.char_combo.set(build.character)
        # Options
        self.deep_var.set(build.include_deep)
        # Tiers
        effects_json = self.effects_json
        for tier_key, tree in self.tier_trees.items():
            tree.delete(*tree.get_children())
            for eff_id in build.tiers.get(tier_key, []):
                name = effects_json.get(str(eff_id), {}).get(
                    "name", f"Effect {eff_id}")
                tree.insert('', 'end', values=(name,),
                            tags=('item', str(eff_id)))

    def _save_current_build(self):
        """Save UI state back to the current build."""
        build = self._get_current_build()
        if not build:
            return
        build.character = self.char_var.get() or build.character
        build.include_deep = self.deep_var.get()

        for tier_key, tree in self.tier_trees.items():
            effect_ids = []
            for item in tree.get_children():
                tags = tree.item(item, 'tags')
                for tag in tags:
                    if tag != 'item' and tag.isdigit():
                        effect_ids.append(int(tag))
            build.tiers[tier_key] = effect_ids

        self.store.update(build)

    def _new_build(self):
        name = simpledialog.askstring("New Build", "Build name:",
                                      parent=self.parent)
        if not name:
            return
        char = self.char_var.get() or CHARACTER_NAMES[0]
        build = self.store.create(name, char)
        self._refresh_build_list()
        # Select the new build
        idx = self._build_ids.index(build.id)
        self.build_combo.current(idx)
        self._on_build_selected()

    def _rename_build(self):
        build = self._get_current_build()
        if not build:
            return
        new_name = simpledialog.askstring(
            "Rename Build", "New name:", initialvalue=build.name,
            parent=self.parent)
        if new_name:
            self.store.rename(build.id, new_name)
            self._refresh_build_list()

    def _delete_build(self):
        build = self._get_current_build()
        if not build:
            return
        if messagebox.askyesno("Delete Build",
                               f"Delete '{build.name}'?"):
            self.store.delete(build.id)
            self._refresh_build_list()

    def _clear_ui(self):
        for tree in self.tier_trees.values():
            tree.delete(*tree.get_children())

    def _on_character_changed(self, event=None):
        self._save_current_build()

    def _on_option_changed(self):
        self._save_current_build()

    # ---- Effect Management ----

    def _add_effect(self, tier_key: str, show_debuffs_first: bool = False):
        build = self._get_current_build()
        if not build:
            messagebox.showinfo("Info", "Create a build first.")
            return

        # Collect already-used effect IDs
        exclude = set()
        for tk_key, tree in self.tier_trees.items():
            for item in tree.get_children():
                for tag in tree.item(item, 'tags'):
                    if tag != 'item' and tag.isdigit():
                        exclude.add(int(tag))

        character = self.char_var.get() or "Wylder"
        effects_list = self._get_effects_list()

        dialog = EffectSearchDialog(
            self.parent, effects_list, character, exclude,
            show_debuffs_first=show_debuffs_first)
        self.parent.wait_window(dialog.dialog)

        if dialog.result:
            eff = dialog.result
            tree = self.tier_trees[tier_key]
            tree.insert('', 'end', values=(eff['name'],),
                        tags=('item', str(eff['id'])))
            self._save_current_build()

    def _remove_effect(self, tier_key: str):
        tree = self.tier_trees[tier_key]
        sel = tree.selection()
        if sel:
            tree.delete(sel[0])
            self._save_current_build()

    # ---- Optimization ----

    def on_inventory_loaded(self, ga_relics: list, relic_checker=None):
        """Called when a save file is loaded and relics are parsed."""
        self.inventory = RelicInventory(
            ga_relics, self.items_json, self.data_source)
        count = len(self.inventory)
        self.status_label.config(
            text=f"Loaded {count} relics. Ready to optimize.",
            foreground='black')

    def _run_optimize(self):
        build = self._get_current_build()
        if not build:
            messagebox.showinfo("Info", "Create or select a build first.")
            return
        if not self.inventory or len(self.inventory) == 0:
            messagebox.showinfo("Info", "Load a save file first.")
            return

        character = self.char_var.get()
        if not character:
            messagebox.showinfo("Info", "Select a character.")
            return

        # Save current state
        self._save_current_build()
        build = self._get_current_build()  # Re-fetch after save

        # Get hero_type (1-indexed)
        hero_type = CHARACTER_NAMES[:10].index(character) + 1

        self.status_label.config(text="Optimizing...", foreground='blue')
        self.parent.update_idletasks()

        results = self.optimizer.optimize_all_vessels(
            build, self.inventory, hero_type)

        self._display_results(results[:3], build)

        if results:
            # Check if best result meets requirements
            if results[0].meets_requirements:
                self.status_label.config(
                    text=f"Done. Best score: {results[0].total_score}",
                    foreground='green')
            else:
                self.status_label.config(
                    text=f"Done. No vessels meet all requirements. Best score: {results[0].total_score}",
                    foreground='orange')
        else:
            self.status_label.config(
                text="No vessels found for this character.",
                foreground='red')

    def _display_results(self, results: list, build: BuildDefinition):
        """Display optimization results in the right panel."""
        # Clear previous results
        for widget in self.results_inner.winfo_children():
            widget.destroy()

        if not results:
            ttk.Label(self.results_inner,
                      text="No results found.",
                      foreground='gray').pack(padx=20, pady=40)
            return

        # Check if any results meet requirements
        has_required = any(r.meets_requirements for r in results)
        required_effects = build.tiers.get("required", [])

        if required_effects and not has_required:
            # Show warning if there are requirements but none are met
            warning_frame = ttk.Frame(self.results_inner)
            warning_frame.pack(fill='x', padx=10, pady=10)
            ttk.Label(
                warning_frame,
                text="⚠ No vessels meet all required properties.",
                foreground='#FF4444',
                font=('TkDefaultFont', 10, 'bold')
            ).pack(anchor='w')
            ttk.Label(
                warning_frame,
                text="Showing best alternatives:",
                foreground='gray'
            ).pack(anchor='w', pady=(2, 0))

        for rank, result in enumerate(results, 1):
            self._create_vessel_card(rank, result, build)

    def _create_vessel_card(self, rank: int, result: VesselResult,
                            build: BuildDefinition):
        """Create a card displaying one vessel's optimization result."""
        # Vessel header - add indicator if requirements not met
        header_text = f"#{rank}: {result.vessel_name} (Score: {result.total_score})"
        if not result.meets_requirements:
            header_text += " ⚠ Missing Requirements"

        card = ttk.LabelFrame(self.results_inner, text=header_text)
        card.pack(fill='x', padx=5, pady=5)

        # Vessel info row
        info_frame = ttk.Frame(card)
        info_frame.pack(fill='x', padx=5, pady=2)

        char_text = result.vessel_character
        lock_text = "Unlocked" if result.unlock_flag == 0 else "Requires Unlock"
        lock_color = 'black' if result.unlock_flag == 0 else '#CC6600'

        ttk.Label(info_frame, text=f"Character: {char_text}").pack(
            side='left', padx=(0, 15))
        ttk.Label(info_frame, text=lock_text,
                  foreground=lock_color).pack(side='left')

        # Show missing requirements if any
        if not result.meets_requirements and result.missing_requirements:
            missing_frame = ttk.Frame(card)
            missing_frame.pack(fill='x', padx=5, pady=(2, 5))
            ttk.Label(
                missing_frame,
                text="Missing Required:",
                foreground='#FF4444',
                font=('TkDefaultFont', 8, 'bold')
            ).pack(anchor='w')
            for eff_id in result.missing_requirements:
                eff_name = self.data_source.get_effect_name(eff_id)
                ttk.Label(
                    missing_frame,
                    text=f"  • {eff_name}",
                    foreground='#FF4444',
                    font=('TkDefaultFont', 8)
                ).pack(anchor='w')

        # Slot assignments
        for assignment in result.assignments:
            self._create_slot_row(card, assignment)

    def _create_slot_row(self, parent, assignment):
        """Create a row for one slot assignment."""
        slot_frame = ttk.Frame(parent)
        slot_frame.pack(fill='x', padx=5, pady=1)

        # Slot label
        slot_type = "Deep " if assignment.is_deep else ""
        slot_num = assignment.slot_index + 1
        color_hex = RELIC_COLOR_HEX.get(assignment.slot_color, '#888888')

        slot_label = tk.Label(
            slot_frame,
            text=f"  Slot {slot_num} ({slot_type}{assignment.slot_color})",
            fg=color_hex, width=22, anchor='w')
        slot_label.pack(side='left')

        if assignment.relic is None:
            ttk.Label(slot_frame, text="(empty - no matching relic)",
                      foreground='gray').pack(side='left', padx=5)
            return

        # Relic name and color
        relic = assignment.relic
        relic_color_hex = RELIC_COLOR_HEX.get(relic.color, '#888888')
        relic_label = tk.Label(
            slot_frame,
            text=f"{relic.name} [{relic.tier}]",
            fg=relic_color_hex, anchor='w')
        relic_label.pack(side='left', padx=(5, 10))

        # Score
        score_label = ttk.Label(slot_frame,
                                text=f"+{assignment.score}",
                                foreground='green' if assignment.score > 0 else 'gray')
        score_label.pack(side='left', padx=5)

        # Effects breakdown (on next line)
        if assignment.breakdown:
            effects_frame = ttk.Frame(parent)
            effects_frame.pack(fill='x', padx=(30, 5), pady=(0, 2))

            for item in assignment.breakdown:
                tier = item.get("tier")
                score = item.get("score", 0)
                is_curse = item.get("is_curse", False)
                redundant = item.get("redundant", False)

                if redundant:
                    fg = '#999999'
                    tier_label = f" [{TIER_DISPLAY.get(tier, '')} (redundant)]"
                elif tier:
                    fg = TIER_COLORS.get(tier, '#888888')
                    tier_label = f" [{TIER_DISPLAY.get(tier, '')} {score:+d}]"
                elif is_curse:
                    fg = '#CC6600'
                    tier_label = " [Curse]"
                else:
                    fg = '#666666'
                    tier_label = ""

                prefix = "Curse: " if is_curse else ""
                eff_label = tk.Label(
                    effects_frame,
                    text=f"  {prefix}{item['name']}{tier_label}",
                    fg=fg, anchor='w', font=('TkDefaultFont', 8))
                eff_label.pack(anchor='w')
