"""Registro dei 100 task del benchmark Computer Use (F3.1.2) sulla fixture Qt.

Ogni task e' un OBIETTIVO utente verificato da uno o piu' test reali (fixture vera, UI Automation,
mouse/tastiera veri); la mappatura e' esplicita, non indovinata dai docstring. Tre tipi:

- `goal`: Jake raggiunge l'obiettivo (es. "rimuovi con conferma");
- `negative`: il comportamento corretto e' NON produrre un effetto e Jake lo riconosce (es. un click
  centrale non aggiunge nulla, un campo in sola lettura resta invariato);
- `limitation`: il test documenta che l'obiettivo NON e' raggiungibile oggi (un limite reale di Qt/UIA).
  Conta come FALLITO nel tasso di successo anche se il test che documenta il limite passa: un limite
  noto non e' un successo.

Il runner e' `benchmarks/bench_computer_use_tasks.py`."""
from dataclasses import dataclass

_I = "tests.test_computer_use_integration."
_S = "tests.test_selector."
_E = "tests.test_executor."
_U = "tests.test_ui_automation_adapter."


@dataclass(frozen=True)
class Task:
    number: int
    title: str
    kind: str  # goal | negative | limitation
    tests: tuple[str, ...]
    area: str


def _t(number, title, kind, area, *tests):
    return Task(number, title, kind, tuple(tests), area)


_KM = _I + "MoreKeyboardShortcutsAcrossFieldsEndToEndTests."
_KF = _I + "MoreKeyboardAndFocusEndToEndTests."
_LK = _I + "ListKeyboardNavigationEndToEndTests."
_MM = _I + "MoreMouseAndFocusEndToEndTests."
_RA = _I + "RadioArrowNavigationAndAccessibilityPropertiesEndToEndTests."
_UM = _I + "UndoAcrossMoreFieldsEndToEndTests."
_RB = _I + "ResetButtonReachesEveryLateFieldEndToEndTests."
_CC = _I + "CheckableListCoverageAndTransferAndComboEndToEndTests."

TASKS: tuple[Task, ...] = (
    _t(1, "scrivi un testo e aggiungilo alla lista", "goal", "input",
       _I + "ClickElementRealFixtureTests.test_type_into_element_then_click_element_completes_task_1_without_manual_assembly"),
    _t(2, "rimuovi un elemento con dialogo di conferma", "goal", "dialog",
       _I + "RemoveWithConfirmationEndToEndTests.test_add_select_remove_and_confirm_completes_for_real"),
    _t(3, "espandi una categoria dell'albero", "goal", "tree",
       _I + "ExpandCategoryEndToEndTests.test_selecting_the_category_then_pressing_right_reveals_its_children_verified_via_ocr_not_uia"),
    _t(4, "cambia scheda e spunta l'opzione", "goal", "tabs",
       _I + "ChangeTabAndToggleEndToEndTests.test_switching_tab_then_toggling_the_option_completes_for_real"),
    _t(5, "scorri e seleziona l'ultima riga", "goal", "scroll",
       _I + "ScrollAndSelectLastRowEndToEndTests.test_pressing_end_scrolls_to_the_bottom_and_selects_the_last_row_for_real"),
    _t(6, "agisci su un controllo che si abilita dopo un'attesa", "goal", "dynamic",
       _I + "DynamicControlEndToEndTests.test_a_dynamically_enabled_control_is_detected_only_after_it_really_becomes_ready"),
    _t(7, "distingui due bottoni con lo stesso nome", "goal", "ambiguous",
       _I + "AmbiguousButtonsEndToEndTests.test_a_name_only_selector_is_rejected_as_ambiguous",
       _I + "AmbiguousButtonsEndToEndTests.test_automation_id_disambiguates_and_the_click_reaches_the_right_button"),
    _t(8, "agisci con il tema scuro", "goal", "theme",
       _S + "ThemeChangeTests.test_the_window_is_really_visually_dark_not_just_a_flag_that_did_nothing",
       _S + "ThemeChangeTests.test_a_name_based_selector_still_finds_and_clicks_correctly_under_the_dark_theme"),
    _t(9, "agisci sull'interfaccia tradotta", "goal", "translation",
       _S + "TranslationChangeTests.test_the_italian_name_no_longer_matches_under_the_english_build",
       _S + "TranslationChangeTests.test_the_translated_name_matches_and_the_click_works",
       _S + "TranslationChangeTests.test_the_automation_id_selector_works_regardless_of_the_current_language"),
    _t(10, "naviga e agisci solo con la tastiera", "goal", "keyboard",
       _I + "KeyboardOnlyNavigationEndToEndTests.test_typing_tabbing_and_pressing_space_adds_an_item_without_ever_clicking_the_button",
       _I + "KeyboardOnlyNavigationEndToEndTests.test_a_single_tab_from_the_input_field_really_focuses_the_add_button_not_something_else"),
    _t(11, "seleziona piu' elementi con Ctrl+Click", "goal", "list",
       _I + "MultiSelectEndToEndTests.test_ctrl_click_selects_two_non_adjacent_items_leaving_the_middle_one_unselected"),
    _t(12, "apri un menu a tendina e scegli un'opzione", "goal", "combo",
       _I + "ComboBoxSelectionEndToEndTests.test_expanding_via_uia_then_clicking_the_popup_item_selects_it_for_real"),
    _t(13, "tasto destro e voce del menu contestuale", "goal", "menu",
       _I + "ContextMenuEndToEndTests.test_right_click_then_pixel_click_on_duplicate_adds_a_real_second_copy"),
    _t(14, "porta un cursore a un valore", "goal", "slider",
       _E + "RangeValueTests.test_setting_the_value_actually_changes_it"),
    _t(15, "aspetta che la barra di avanzamento arrivi al 100%", "goal", "progress",
       _I + "ProgressBarEndToEndTests.test_the_progress_value_climbs_through_real_intermediate_values_to_one_hundred"),
    _t(16, "trascina un elemento per riordinare una lista", "goal", "drag",
       _I + "DragReorderEndToEndTests.test_dragging_the_first_item_past_the_second_really_reorders_the_list"),
    _t(17, "imposta il valore di uno spinbox", "goal", "spinbox",
       _E + "RangeValueOnSpinBoxTests.test_setting_the_value_actually_changes_it"),
    _t(18, "scegli un'opzione da un gruppo esclusivo", "goal", "radio",
       _E + "RadioButtonMutualExclusivityTests.test_selecting_a_different_radio_really_deselects_the_previous_one"),
    _t(19, "modifica una cella di tabella", "goal", "table",
       _I + "TableCellEditEndToEndTests.test_clicking_a_cell_and_typing_replaces_its_text",
       _I + "TableCellEditEndToEndTests.test_a_single_pixel_click_selects_and_focuses_the_cell"),
    _t(20, "trascina un elemento da una lista a un'altra", "goal", "drag",
       _I + "CrossListDragEndToEndTests.test_dragging_an_item_from_the_source_list_moves_it_to_the_target_list"),
    _t(21, "seleziona un intervallo con Shift+Click", "goal", "list",
       _I + "MultiSelectEndToEndTests.test_shift_click_selects_a_contiguous_range_including_the_middle_item"),
    _t(22, "scegli una data dal calendario a comparsa", "goal", "date",
       _I + "DateEditCalendarPopupEndToEndTests.test_navigating_the_popup_with_arrow_keys_and_enter_changes_the_date"),
    _t(23, "filtra la lista mentre scrivi", "goal", "filter",
       _I + "LiveFilterEndToEndTests.test_typing_a_substring_hides_non_matching_items_for_real",
       _I + "LiveFilterEndToEndTests.test_clearing_the_filter_makes_hidden_items_reachable_again"),
    _t(24, "Ctrl+Z annulla il testo scritto", "goal", "keyboard",
       _I + "TextEditingShortcutsEndToEndTests.test_ctrl_z_undoes_typed_text_back_to_empty"),
    _t(25, "Ctrl+A e Canc svuotano il campo", "goal", "keyboard",
       _I + "TextEditingShortcutsEndToEndTests.test_ctrl_a_then_delete_clears_the_field"),
    _t(26, "Esc chiude il popup senza applicare", "goal", "combo",
       _I + "EscapeCancelsThePopupEndToEndTests.test_escape_closes_the_combo_popup_without_applying_the_highlighted_option"),
    _t(27, "scrivi su piu' righe", "goal", "input",
       _I + "MultilineEditEndToEndTests.test_typing_two_lines_with_enter_preserves_the_newline"),
    _t(28, "minimizza e ripristina la finestra", "goal", "window",
       _E + "WindowPatternTests.test_minimizing_then_restoring_really_changes_the_visual_state"),
    _t(29, "annulla un'operazione lunga a meta'", "goal", "progress",
       _I + "ProgressBarEndToEndTests.test_cancel_mid_progress_stops_it_at_the_current_value"),
    _t(30, "scrivi in un combo editabile", "goal", "combo",
       _I + "EditableComboEndToEndTests.test_typing_into_the_inner_edit_child_changes_the_combo_value"),
    _t(31, "Ctrl+A seleziona tutta la lista", "goal", "list",
       _I + "MultiSelectEndToEndTests.test_ctrl_a_selects_every_item_in_the_list"),
    _t(32, "Shift+Tab torna al controllo precedente", "goal", "keyboard",
       _KF + "test_shift_tab_moves_focus_back_to_the_previous_control"),
    _t(33, "Home/Fine portano il cursore agli estremi", "goal", "slider",
       _KF + "test_home_and_end_move_the_slider_to_its_minimum_and_maximum"),
    _t(34, "PagSu avanza il cursore di un passo pagina", "goal", "slider",
       _KF + "test_page_up_advances_the_slider_by_a_full_page_step"),
    _t(35, "Home/Fine nello spinbox muovono solo il cursore di testo", "goal", "spinbox",
       _KF + "test_home_and_end_move_the_cursor_in_a_spinbox_without_changing_its_value"),
    _t(36, "frecce su/giu' cambiano lo spinbox di uno", "goal", "spinbox",
       _KF + "test_up_and_down_arrows_change_the_spinbox_value_by_one"),
    _t(37, "Spazio spunta la casella con il fuoco", "goal", "keyboard",
       _KF + "test_space_toggles_the_checkbox_when_it_has_focus"),
    _t(38, "tasto destro nel vuoto non apre menu", "negative", "menu",
       _KF + "test_right_clicking_empty_space_in_the_list_opens_no_context_menu"),
    _t(39, "leggi il tooltip di un bottone", "limitation", "accessibility",
       _I + "ToolTipKnownLimitationEndToEndTests.test_current_help_text_stays_empty_despite_a_real_qt_tooltip"),
    _t(40, "svuota un campo multiriga", "goal", "input",
       _I + "MultilineEditEndToEndTests.test_ctrl_a_then_delete_clears_multiline_text"),
    _t(41, "un campo in sola lettura resta invariato", "negative", "input",
       _I + "ReadOnlyFieldEndToEndTests.test_typing_into_the_field_has_no_effect"),
    _t(42, "porta una casella a tre stati su indeterminato", "limitation", "checkbox",
       _I + "TristateCheckboxEndToEndTests.test_uia_toggle_never_reaches_the_indeterminate_state"),
    _t(43, "una lista senza selezione non seleziona", "negative", "list",
       _I + "NoSelectionListEndToEndTests.test_clicking_an_item_never_selects_it"),
    _t(44, "freccia giu' sposta la selezione", "goal", "list", _LK + "test_down_arrow_moves_the_selection_to_the_next_item"),
    _t(45, "Fine salta all'ultimo elemento", "goal", "list", _LK + "test_end_jumps_the_selection_to_the_last_item"),
    _t(46, "Home salta al primo elemento", "goal", "list", _LK + "test_home_jumps_the_selection_to_the_first_item"),
    _t(47, "freccia su torna all'elemento precedente", "goal", "list",
       _LK + "test_up_arrow_moves_the_selection_back_to_the_previous_item"),
    _t(48, "Tab nel campo multiriga inserisce una tabulazione", "goal", "keyboard",
       _I + "TabDoesNotChangeFocusInsideMultilineEditEndToEndTests.test_tab_inserts_a_literal_tab_character_instead_of_moving_focus"),
    _t(49, "Esc non cancella la selezione della lista", "negative", "list",
       _LK + "test_escape_does_not_clear_the_list_selection"),
    _t(50, "Ctrl+Click su un elemento selezionato lo deseleziona", "goal", "list",
       _LK + "test_ctrl_click_on_an_already_selected_item_deselects_it"),
    _t(51, "leggi le capacita' di spostamento/ridimensionamento", "goal", "window",
       _E + "TransformPatternTests.test_the_window_reports_it_can_move_and_resize_but_not_rotate"),
    _t(52, "ridimensiona la finestra", "goal", "window", _E + "TransformPatternTests.test_resizing_the_window_really_changes_its_width"),
    _t(53, "sposta la finestra", "goal", "window", _E + "TransformPatternTests.test_moving_the_window_really_changes_its_position"),
    _t(54, "spunta una voce di lista con casella", "goal", "checkbox",
       _I + "CheckableListItemEndToEndTests.test_a_pixel_click_on_the_checkbox_glyph_really_checks_the_item",
       _I + "CheckableListItemEndToEndTests.test_uia_toggle_has_no_real_effect_on_a_checkable_list_item"),
    _t(55, "un campo password non espone il testo", "goal", "privacy",
       _I + "PasswordFieldEndToEndTests.test_uia_value_shows_masked_characters_not_the_real_password"),
    _t(56, "un campo numerico rifiuta lettere e fuori intervallo", "negative", "validation",
       _I + "ValidatedNumericFieldEndToEndTests.test_typing_letters_and_an_out_of_range_digit_are_both_rejected"),
    _t(57, "attiva un bottone a interruttore", "goal", "toggle",
       _I + "ToggleToolButtonEndToEndTests.test_uia_toggle_really_checks_the_tool_button"),
    _t(58, "riconosci un indicatore di attivita' indeterminato", "goal", "progress",
       _I + "BusyIndicatorEndToEndTests.test_range_value_reports_the_indeterminate_signature"),
    _t(59, "usa una scorciatoia globale (Ctrl+N)", "goal", "keyboard",
       _I + "GlobalShortcutEndToEndTests.test_ctrl_n_adds_the_current_input_text_to_the_list"),
    _t(60, "rotella del mouse sul combo", "goal", "mouse",
       _MM + "test_scrolling_the_mouse_wheel_over_the_combo_changes_the_selection"),
    _t(61, "un click centrale non attiva il bottone", "negative", "mouse", _MM + "test_middle_click_on_add_does_not_add_anything"),
    _t(62, "Tab salta il bottone disabilitato", "goal", "disabled", _MM + "test_tab_order_skips_the_disabled_remove_button"),
    _t(63, "usa un sottomenu contestuale", "goal", "menu",
       _I + "ContextMenuSubmenuAndDisabledItemEndToEndTests.test_clicking_the_submenu_then_its_item_really_applies_the_action"),
    _t(64, "riconosci una voce di menu disabilitata", "negative", "disabled",
       _I + "ContextMenuSubmenuAndDisabledItemEndToEndTests.test_delete_all_is_reported_as_disabled"),
    _t(65, "frecce tra pulsanti radio", "goal", "radio", _RA + "test_down_arrow_moves_focus_and_selection_to_the_next_radio_button"),
    _t(66, "leggi le proprieta' di accessibilita'", "goal", "accessibility",
       _RA + "test_the_add_button_reports_standard_accessibility_properties"),
    _t(67, "Canc sulla lista non rimuove nulla", "negative", "list", _RA + "test_delete_key_on_the_list_has_no_effect"),
    _t(68, "riconosci il campo password", "goal", "privacy", _RA + "test_is_password_property_is_true_only_for_the_password_field"),
    _t(69, "spunta una voce di lista con la tastiera", "limitation", "checkbox",
       _I + "CheckableListSpaceKeyKnownLimitationEndToEndTests.test_space_key_does_not_check_the_item"),
    _t(70, "recupera da un errore COM transitorio", "goal", "recovery",
       _U + "RetryTransientComErrorTests.test_retries_and_recovers_from_a_transient_value_error",
       _U + "RetryTransientComErrorTests.test_an_unrelated_error_propagates_immediately_without_retrying"),
    _t(71, "PagSu/Su cambiano l'anno di una data", "goal", "date", _KM + "test_page_up_and_up_on_a_fresh_date_edit_change_the_year_section"),
    _t(72, "svuota il campo numerico", "goal", "keyboard", _KM + "test_ctrl_a_then_delete_clears_the_numeric_field"),
    _t(73, "Esc non annulla il testo nel combo editabile", "negative", "combo",
       _KM + "test_escape_does_not_revert_freshly_typed_text_in_the_editable_combo"),
    _t(74, "scrivi cifre nello spinbox", "goal", "spinbox", _KM + "test_typing_digits_directly_sets_the_spinbox_value"),
    _t(75, "svuota il campo password", "goal", "privacy", _KM + "test_ctrl_a_then_delete_clears_the_password_field"),
    _t(76, "Ctrl+Y ripete l'annullato", "goal", "keyboard", _KM + "test_ctrl_y_redoes_what_ctrl_z_just_undid"),
    _t(77, "riconosci il framework dell'app", "goal", "accessibility", _KM + "test_the_framework_id_is_qt_for_a_real_control"),
    _t(78, "tasto destro sulla lista sorgente non apre menu", "negative", "menu",
       _KM + "test_right_clicking_the_transfer_source_list_opens_no_context_menu"),
    _t(79, "verifica che Ctrl+A selezioni tutto il testo", "goal", "keyboard",
       _KM + "test_text_pattern_confirms_ctrl_a_really_selects_everything"),
    _t(80, "Esc non svuota il campo di ricerca", "negative", "filter", _KM + "test_escape_does_not_clear_the_search_field"),
    _t(81, "Esc nel calendario non annulla la data navigata", "negative", "date",
       _KM + "test_escape_in_the_calendar_popup_does_not_revert_the_navigated_date"),
    _t(82, "il filtro si aggiorna a ogni carattere", "goal", "filter",
       _KM + "test_the_filter_updates_incrementally_as_each_character_is_typed"),
    _t(83, "Ctrl+Backspace cancella la parola precedente", "goal", "keyboard", _KM + "test_ctrl_backspace_deletes_the_previous_word"),
    _t(84, "leggi righe/colonne di una tabella", "goal", "table",
       _I + "GridPatternEndToEndTests.test_row_and_column_count_and_get_item_match_the_real_table"),
    _t(85, "una cella fuori tabella non fa crashare", "negative", "table",
       _I + "GridPatternEndToEndTests.test_get_item_out_of_range_returns_an_invalid_element_not_a_crash"),
    _t(86, "le frecce non riordinano la lista", "negative", "list",
       _I + "ReorderListArrowKeysDoNotReorderEndToEndTests.test_down_home_and_end_never_change_the_order"),
    _t(87, "Giu'/PagGiu' cambiano l'anno di una data", "goal", "date",
       _I + "MoreDateEditAndCheckableListEndToEndTests.test_down_and_page_down_decrement_the_year_section"),
    _t(88, "spunta e togli la spunta a una voce", "goal", "checkbox",
       _I + "MoreDateEditAndCheckableListEndToEndTests.test_clicking_the_glyph_twice_checks_then_unchecks_the_item"),
    _t(89, "Ctrl+Z nel campo multiriga", "goal", "keyboard", _UM + "test_ctrl_z_undoes_typed_text_in_the_multiline_field"),
    _t(90, "Ctrl+Z nel combo editabile", "goal", "keyboard", _UM + "test_ctrl_z_undoes_typed_text_in_the_editable_combo"),
    _t(91, "Ctrl+Z nel campo numerico", "goal", "keyboard", _UM + "test_ctrl_z_undoes_typed_text_in_the_numeric_field"),
    _t(92, "Ctrl+Z nel campo password", "goal", "keyboard", _UM + "test_ctrl_z_undoes_typed_text_in_the_password_field"),
    _t(93, "spunta la seconda voce indipendentemente", "goal", "checkbox",
       _CC + "test_the_second_checkable_item_can_be_checked_independently"),
    _t(94, "spunta la terza voce della lista", "limitation", "checkbox",
       _CC + "test_the_third_checkable_item_is_a_known_unreachable_edge_case"),
    _t(95, "trascina due elementi in sequenza", "goal", "drag",
       _CC + "test_dragging_both_items_leaves_the_source_list_completely_empty"),
    _t(96, "svuota il combo editabile", "goal", "keyboard", _CC + "test_ctrl_a_then_delete_clears_the_editable_combo"),
    _t(97, "Reset ripristina la data", "goal", "recovery", _RB + "test_reset_restores_the_date_edit_after_keyboard_navigation"),
    _t(98, "Reset ripristina il combo editabile", "goal", "recovery", _RB + "test_reset_restores_the_editable_combo"),
    _t(99, "Reset svuota il campo numerico", "goal", "recovery", _RB + "test_reset_clears_the_numeric_field"),
    _t(100, "Reset svuota il campo password", "goal", "recovery", _RB + "test_reset_clears_the_password_field"),
)

KINDS = frozenset({"goal", "negative", "limitation"})
