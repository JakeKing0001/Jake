"""Costruzione delle skill built-in di Jake, raggruppate per dominio (v3.2).

Prima erano tutte istanziate in un unico dizionario dentro SkillRegistry.__init__
(~180 voci in un blocco solo). Qui ogni funzione build_*_skills() costruisce e
restituisce il sotto-dizionario di un dominio: SkillRegistry le unisce tutte.
Nessun comportamento e' cambiato, e' solo la stessa lista riorganizzata per
essere leggibile e testabile a pezzi piu' piccoli."""

from skills.time import TimeSkill
from skills.date import DateSkill
from skills.datetime_utils import GetDayOfWeekSkill, DaysUntilSkill, GetWeekNumberSkill, ConvertTimezoneSkill

from skills.open_app import OpenAppSkill
from skills.window_control import FocusWindowSkill, MinimizeWindowSkill
from skills.process_control import ListProcessesSkill, CloseAppSkill
from skills.window_layout import (
    ListOpenWindowsSkill, MaximizeWindowSkill, RestoreWindowSkill, SnapWindowLeftSkill,
    SnapWindowRightSkill, SwitchNextWindowSkill, MinimizeAllWindowsSkill,
    SetWindowAlwaysOnTopSkill, ResizeWindowSkill,
)
from skills.close_window import CloseWindowSkill
from skills.active_window import GetActiveWindowSkill

from skills.open_path import OpenPathSkill
from skills.create_path import CreatePathSkill
from skills.rename_path import RenamePathSkill
from skills.move_path import MovePathSkill
from skills.delete_path import DeletePathSkill
from skills.find_file import FindFileSkill
from skills.open_search_result import OpenSearchResultSkill
from skills.semantic_search_files import SemanticSearchFilesSkill
from skills.hybrid_search_files import HybridSearchFilesSkill
from skills.build_semantic_index import BuildSemanticIndexSkill
from skills.file_utils import (
    CompressPathSkill, ExtractArchiveSkill, GetFileInfoSkill, CountWordsInFileSkill,
    ReadFileTextSkill, DuplicateFileSkill, GetFolderSizeSkill,
)
from skills.file_utils2 import FindLargeFilesSkill, FindDuplicateFilesSkill
from skills.app_utils import ListRecentFilesSkill, OpenIncognitoWindowSkill, EmptyClipboardSkill
from skills.print_utils import PrintFileSkill

from skills.web_search import WebSearchSkill
from skills.get_weather import GetWeatherSkill
from skills.get_news import GetNewsSkill
from skills.open_url import OpenUrlSkill
from skills.browser_history import GetBrowserHistorySkill
from skills.network_utils import PingHostSkill, TraceRouteSkill, CheckWebsiteStatusSkill
from skills.browser_search import SearchInBrowserSkill, PlayMediaSkill

from skills.remember import RememberSkill
from skills.recall import RecallSkill
from skills.forget import ForgetSkill
from skills.link_memory import LinkMemorySkill
from skills.notes import AddNoteSkill, ListNotesSkill, SearchNotesSkill, ExportNotesSkill, ClearNotesSkill
from skills.todo import AddTodoSkill, ListTodosSkill, CompleteTodoSkill, DeleteTodoSkill

from skills.workflow import SaveWorkflowSkill, RunWorkflowSkill
from skills.trigger import SetTriggerSkill, ListTriggersSkill, DeleteTriggerSkill
from skills.reminder import SetReminderSkill, ListRemindersSkill
from skills.reminders_extra import (
    SnoozeReminderSkill, DeleteReminderSkill, SetDailyReminderSkill, StartPomodoroSkill, StopPomodoroSkill,
)
from skills.timer import SetTimerSkill, CancelTimerSkill, ListTimersSkill

from skills.screenshot import TakeScreenshotSkill
from skills.read_screen import ReadScreenSkill
from skills.describe_screen import DescribeScreenSkill
from skills.mouse_control import ClickMouseSkill, MoveMouseSkill
from skills.keyboard_control import TypeTextSkill, PressKeySkill
from skills.screen_click import ClickTextSkill, ClickElementSkill, ScrollSkill
from skills.read_selection import ReadSelectionSkill

from skills.volume_control import VolumeControlSkill
from skills.volume_level import SetVolumeLevelSkill, GetVolumeLevelSkill
from skills.media_control import MediaControlSkill

from skills.system_power import SystemPowerSkill
from skills.brightness_control import SetBrightnessSkill
from skills.recycle_bin import EmptyRecycleBinSkill
from skills.wifi_control import ListWifiNetworksSkill
from skills.system_info import (
    GetBatteryStatusSkill, GetCpuUsageSkill, GetMemoryUsageSkill, GetDiskUsageSkill,
    GetUptimeSkill, GetSystemInfoSkill, GetLocalIpSkill, GetPublicIpSkill,
)
from skills.system_maintenance import (
    ClearTempFilesSkill, RestartExplorerSkill, FlushDnsSkill, ListStartupAppsSkill,
    ListInstalledAppsSkill, SetPowerPlanSkill, ToggleDarkModeSkill, GetWifiStatusSkill,
)
from skills.system_info2 import (
    GetScreenResolutionSkill, GetGpuInfoSkill, GetDnsServersSkill,
    GetEnvironmentVariableSkill, GetMacAddressSkill, ListDrivesSkill,
)

from skills.git_control import GitStatusSkill, GitPullSkill, GitLogSkill, GitBranchSkill, GitDiffSkill
from skills.editor_control import OpenInEditorSkill
from skills.run_command import RunCommandSkill
from skills.dev_tools import (
    RunPythonScriptSkill, FormatJsonSkill, CountLinesOfCodeSkill, GenerateUuidSkill,
    CheckPortInUseSkill, KillProcessByPortSkill,
)
from skills.security_utils import CheckPasswordStrengthSkill, CheckFileHashSkill

from skills.ask_question import AskQuestionSkill
from skills.translate_text import TranslateTextSkill
from skills.calculate import CalculateSkill
from skills.convert_units import ConvertUnitsSkill
from skills.summarize_clipboard import SummarizeClipboardSkill
from skills.clipboard import ClipboardReadSkill, ClipboardWriteSkill, TranslateClipboardSkill
from skills.text_utils import (
    CountWordsSkill, ConvertCaseSkill, GeneratePasswordSkill, ProofreadTextSkill,
    SummarizeTextSkill, DetectLanguageSkill, ExtractUrlsFromTextSkill,
    ConvertNumberToWordsSkill, ConvertRomanNumeralSkill, ConvertMorseCodeSkill,
)
from skills.personal_utils import CalculateBmiSkill, CalculateTipSkill, CalculateAgeSkill, CalculateDiscountSkill
from skills.math_utils2 import IsPrimeSkill, FibonacciSkill, GcdLcmSkill, CalculatePercentageSkill, RandomNumberSkill

from skills.fun import TellJokeSkill, RollDiceSkill, FlipCoinSkill
from skills.fun2 import RandomQuoteSkill, RandomFactSkill, Magic8BallSkill, ChooseRandomSkill, RockPaperScissorsSkill

from skills.astronomy import GetSunriseSunsetSkill, GetMoonPhaseSkill
from skills.currency import ConvertCurrencySkill
from skills.holidays import GetNextHolidaySkill

from skills.research import ResearchSkill

from skills.chitchat import ChitChatSkill
from skills.contacts import SaveContactSkill, ListContactsSkill, SendWhatsAppSkill, SendEmailSkill


def build_time_date_skills() -> dict:
    return {
        "GET_TIME": TimeSkill(),
        "GET_DATE": DateSkill(),
        "GET_DAY_OF_WEEK": GetDayOfWeekSkill(),
        "DAYS_UNTIL": DaysUntilSkill(),
        "GET_WEEK_NUMBER": GetWeekNumberSkill(),
        "CONVERT_TIMEZONE": ConvertTimezoneSkill(),
    }


def build_app_window_skills() -> dict:
    return {
        "OPEN_APP": OpenAppSkill(),
        "FOCUS_WINDOW": FocusWindowSkill(),
        "MINIMIZE_WINDOW": MinimizeWindowSkill(),
        "LIST_PROCESSES": ListProcessesSkill(),
        "CLOSE_APP": CloseAppSkill(),
        "GET_ACTIVE_WINDOW": GetActiveWindowSkill(),
        "LIST_OPEN_WINDOWS": ListOpenWindowsSkill(),
        "MAXIMIZE_WINDOW": MaximizeWindowSkill(),
        "RESTORE_WINDOW": RestoreWindowSkill(),
        "SNAP_WINDOW_LEFT": SnapWindowLeftSkill(),
        "SNAP_WINDOW_RIGHT": SnapWindowRightSkill(),
        "SWITCH_NEXT_WINDOW": SwitchNextWindowSkill(),
        "MINIMIZE_ALL_WINDOWS": MinimizeAllWindowsSkill(),
        "SET_WINDOW_ALWAYS_ON_TOP": SetWindowAlwaysOnTopSkill(),
        "RESIZE_WINDOW": ResizeWindowSkill(),
        "CLOSE_WINDOW": CloseWindowSkill(),
    }


def build_filesystem_skills(nest_client, conversation_state, search_files_skill) -> dict:
    return {
        "OPEN_PATH": OpenPathSkill(),
        "CREATE_PATH": CreatePathSkill(),
        "RENAME_PATH": RenamePathSkill(),
        "MOVE_PATH": MovePathSkill(),
        "DELETE_PATH": DeletePathSkill(),
        "FIND_FILE": FindFileSkill(),
        "SEARCH_FILES": search_files_skill,
        "OPEN_SEARCH_RESULT": OpenSearchResultSkill(conversation_state),
        "SEMANTIC_SEARCH_FILES": SemanticSearchFilesSkill(nest_client, conversation_state),
        "HYBRID_SEARCH_FILES": HybridSearchFilesSkill(nest_client, conversation_state),
        "BUILD_SEMANTIC_INDEX": BuildSemanticIndexSkill(nest_client),
        "COMPRESS_PATH": CompressPathSkill(),
        "EXTRACT_ARCHIVE": ExtractArchiveSkill(),
        "GET_FILE_INFO": GetFileInfoSkill(),
        "COUNT_WORDS_IN_FILE": CountWordsInFileSkill(),
        "READ_FILE_TEXT": ReadFileTextSkill(),
        "DUPLICATE_FILE": DuplicateFileSkill(),
        "GET_FOLDER_SIZE": GetFolderSizeSkill(),
        "FIND_LARGE_FILES": FindLargeFilesSkill(),
        "FIND_DUPLICATE_FILES": FindDuplicateFilesSkill(),
        "LIST_RECENT_FILES": ListRecentFilesSkill(),
        "PRINT_FILE": PrintFileSkill(),
    }


def build_web_skills(config, web_search_skill) -> dict:
    return {
        "WEB_SEARCH": web_search_skill,
        "GET_WEATHER": GetWeatherSkill(config),
        "GET_NEWS": GetNewsSkill(config),
        "OPEN_URL": OpenUrlSkill(),
        "GET_BROWSER_HISTORY": GetBrowserHistorySkill(),
        "PING_HOST": PingHostSkill(),
        "TRACE_ROUTE": TraceRouteSkill(),
        "CHECK_WEBSITE_STATUS": CheckWebsiteStatusSkill(),
        "SEARCH_IN_BROWSER": SearchInBrowserSkill(),
        "PLAY_MEDIA": PlayMediaSkill(),
        "OPEN_INCOGNITO_WINDOW": OpenIncognitoWindowSkill(),
    }


def build_memory_notes_todo_skills(memory_manager, embedding_provider, todo_manager) -> dict:
    return {
        "REMEMBER": RememberSkill(memory_manager, embedding_provider),
        "RECALL": RecallSkill(memory_manager, embedding_provider),
        "FORGET": ForgetSkill(memory_manager),
        "LINK_MEMORY": LinkMemorySkill(memory_manager),
        "ADD_NOTE": AddNoteSkill(),
        "LIST_NOTES": ListNotesSkill(),
        "SEARCH_NOTES": SearchNotesSkill(),
        "EXPORT_NOTES": ExportNotesSkill(),
        "CLEAR_NOTES": ClearNotesSkill(),
        "ADD_TODO": AddTodoSkill(todo_manager),
        "LIST_TODOS": ListTodosSkill(todo_manager),
        "COMPLETE_TODO": CompleteTodoSkill(todo_manager),
        "DELETE_TODO": DeleteTodoSkill(todo_manager),
    }


def build_automation_skills(reminder_manager, planner_provider, workflow_manager, trigger_manager, plan_executor) -> dict:
    return {
        "SAVE_WORKFLOW": SaveWorkflowSkill(planner_provider, workflow_manager),
        "RUN_WORKFLOW": RunWorkflowSkill(workflow_manager, plan_executor),
        "SET_TRIGGER": SetTriggerSkill(trigger_manager),
        "LIST_TRIGGERS": ListTriggersSkill(trigger_manager),
        "DELETE_TRIGGER": DeleteTriggerSkill(trigger_manager),
        "SET_REMINDER": SetReminderSkill(reminder_manager),
        "LIST_REMINDERS": ListRemindersSkill(reminder_manager),
        "SNOOZE_REMINDER": SnoozeReminderSkill(reminder_manager),
        "DELETE_REMINDER": DeleteReminderSkill(reminder_manager),
        "SET_DAILY_REMINDER": SetDailyReminderSkill(reminder_manager),
        "START_POMODORO": StartPomodoroSkill(reminder_manager),
        "STOP_POMODORO": StopPomodoroSkill(reminder_manager),
        "SET_TIMER": SetTimerSkill(reminder_manager),
        "CANCEL_TIMER": CancelTimerSkill(reminder_manager),
        "LIST_TIMERS": ListTimersSkill(reminder_manager),
    }


def build_screen_input_skills(vision_provider) -> dict:
    return {
        "TAKE_SCREENSHOT": TakeScreenshotSkill(),
        "READ_SCREEN": ReadScreenSkill(),
        "DESCRIBE_SCREEN": DescribeScreenSkill(vision_provider),
        "CLICK_MOUSE": ClickMouseSkill(),
        "MOVE_MOUSE": MoveMouseSkill(),
        "TYPE_TEXT": TypeTextSkill(),
        "PRESS_KEY": PressKeySkill(),
        "CLICK_TEXT": ClickTextSkill(),
        "CLICK_ELEMENT": ClickElementSkill(vision_provider),
        "SCROLL": ScrollSkill(),
        "READ_SELECTION": ReadSelectionSkill(),
    }


def build_media_skills() -> dict:
    return {
        "SET_VOLUME": VolumeControlSkill(),
        "MEDIA_CONTROL": MediaControlSkill(),
        "SET_VOLUME_LEVEL": SetVolumeLevelSkill(),
        "GET_VOLUME_LEVEL": GetVolumeLevelSkill(),
    }


def build_system_skills() -> dict:
    return {
        "SYSTEM_POWER": SystemPowerSkill(),
        "SET_BRIGHTNESS": SetBrightnessSkill(),
        "EMPTY_RECYCLE_BIN": EmptyRecycleBinSkill(),
        "LIST_WIFI_NETWORKS": ListWifiNetworksSkill(),
        "GET_WIFI_STATUS": GetWifiStatusSkill(),
        "GET_BATTERY_STATUS": GetBatteryStatusSkill(),
        "GET_CPU_USAGE": GetCpuUsageSkill(),
        "GET_MEMORY_USAGE": GetMemoryUsageSkill(),
        "GET_DISK_USAGE": GetDiskUsageSkill(),
        "GET_UPTIME": GetUptimeSkill(),
        "GET_SYSTEM_INFO": GetSystemInfoSkill(),
        "GET_LOCAL_IP": GetLocalIpSkill(),
        "GET_PUBLIC_IP": GetPublicIpSkill(),
        "CLEAR_TEMP_FILES": ClearTempFilesSkill(),
        "RESTART_EXPLORER": RestartExplorerSkill(),
        "FLUSH_DNS": FlushDnsSkill(),
        "LIST_STARTUP_APPS": ListStartupAppsSkill(),
        "LIST_INSTALLED_APPS": ListInstalledAppsSkill(),
        "SET_POWER_PLAN": SetPowerPlanSkill(),
        "TOGGLE_DARK_MODE": ToggleDarkModeSkill(),
        "GET_SCREEN_RESOLUTION": GetScreenResolutionSkill(),
        "GET_GPU_INFO": GetGpuInfoSkill(),
        "GET_DNS_SERVERS": GetDnsServersSkill(),
        "GET_ENVIRONMENT_VARIABLE": GetEnvironmentVariableSkill(),
        "GET_MAC_ADDRESS": GetMacAddressSkill(),
        "LIST_DRIVES": ListDrivesSkill(),
    }


def build_dev_tools_skills() -> dict:
    return {
        "GIT_STATUS": GitStatusSkill(),
        "GIT_PULL": GitPullSkill(),
        "GIT_LOG": GitLogSkill(),
        "GIT_BRANCH": GitBranchSkill(),
        "GIT_DIFF": GitDiffSkill(),
        "OPEN_IN_EDITOR": OpenInEditorSkill(),
        "RUN_COMMAND": RunCommandSkill(),
        "RUN_PYTHON_SCRIPT": RunPythonScriptSkill(),
        "FORMAT_JSON": FormatJsonSkill(),
        "COUNT_LINES_OF_CODE": CountLinesOfCodeSkill(),
        "GENERATE_UUID": GenerateUuidSkill(),
        "CHECK_PORT_IN_USE": CheckPortInUseSkill(),
        "KILL_PROCESS_BY_PORT": KillProcessByPortSkill(),
        "CHECK_PASSWORD_STRENGTH": CheckPasswordStrengthSkill(),
        "CHECK_FILE_HASH": CheckFileHashSkill(),
    }


def build_text_and_math_skills(config, conversation_state) -> dict:
    return {
        "ASK_QUESTION": AskQuestionSkill(conversation_state, model=config.get("ollama_model", "qwen2.5:7b")),
        "TRANSLATE_TEXT": TranslateTextSkill(model=config.get("ollama_model", "qwen2.5:7b")),
        "CALCULATE": CalculateSkill(),
        "CONVERT_UNITS": ConvertUnitsSkill(),
        "SUMMARIZE_CLIPBOARD": SummarizeClipboardSkill(model=config.get("ollama_model", "qwen2.5:7b")),
        "CLIPBOARD_READ": ClipboardReadSkill(),
        "CLIPBOARD_WRITE": ClipboardWriteSkill(),
        "TRANSLATE_CLIPBOARD": TranslateClipboardSkill(model=config.get("ollama_model", "qwen2.5:7b")),
        "EMPTY_CLIPBOARD": EmptyClipboardSkill(),
        "COUNT_WORDS": CountWordsSkill(),
        "CONVERT_CASE": ConvertCaseSkill(),
        "GENERATE_PASSWORD": GeneratePasswordSkill(),
        "PROOFREAD_TEXT": ProofreadTextSkill(model=config.get("ollama_model", "qwen2.5:7b")),
        "SUMMARIZE_TEXT": SummarizeTextSkill(model=config.get("ollama_model", "qwen2.5:7b")),
        "DETECT_LANGUAGE": DetectLanguageSkill(model=config.get("ollama_model", "qwen2.5:7b")),
        "EXTRACT_URLS_FROM_TEXT": ExtractUrlsFromTextSkill(),
        "CONVERT_NUMBER_TO_WORDS": ConvertNumberToWordsSkill(),
        "CONVERT_ROMAN_NUMERAL": ConvertRomanNumeralSkill(),
        "CONVERT_MORSE_CODE": ConvertMorseCodeSkill(),
        "CALCULATE_BMI": CalculateBmiSkill(),
        "CALCULATE_TIP": CalculateTipSkill(),
        "CALCULATE_AGE": CalculateAgeSkill(),
        "CALCULATE_DISCOUNT": CalculateDiscountSkill(),
        "IS_PRIME": IsPrimeSkill(),
        "FIBONACCI": FibonacciSkill(),
        "GCD_LCM": GcdLcmSkill(),
        "CALCULATE_PERCENTAGE": CalculatePercentageSkill(),
        "RANDOM_NUMBER": RandomNumberSkill(),
    }


def build_fun_skills() -> dict:
    return {
        "TELL_JOKE": TellJokeSkill(),
        "ROLL_DICE": RollDiceSkill(),
        "FLIP_COIN": FlipCoinSkill(),
        "RANDOM_QUOTE": RandomQuoteSkill(),
        "RANDOM_FACT": RandomFactSkill(),
        "MAGIC_8_BALL": Magic8BallSkill(),
        "CHOOSE_RANDOM": ChooseRandomSkill(),
        "ROCK_PAPER_SCISSORS": RockPaperScissorsSkill(),
    }


def build_misc_skills() -> dict:
    return {
        "GET_SUNRISE_SUNSET": GetSunriseSunsetSkill(),
        "GET_MOON_PHASE": GetMoonPhaseSkill(),
        "CONVERT_CURRENCY": ConvertCurrencySkill(),
        "GET_NEXT_HOLIDAY": GetNextHolidaySkill(),
    }


def build_research_skills(config, web_search_skill, search_files_skill) -> dict:
    return {
        "RESEARCH": ResearchSkill(
            web_search_skill, search_files_skill, model=config.get("ollama_model", "qwen2.5:7b")
        ),
    }


def build_communication_skills(ollama_client, model, contact_book) -> dict:
    return {
        "CHITCHAT": ChitChatSkill(ollama_client, model=model),
        "SAVE_CONTACT": SaveContactSkill(contact_book),
        "LIST_CONTACTS": ListContactsSkill(contact_book),
        "SEND_WHATSAPP": SendWhatsAppSkill(contact_book),
        "SEND_EMAIL": SendEmailSkill(contact_book),
    }
