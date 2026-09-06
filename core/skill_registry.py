from skills.time import TimeSkill
from skills.date import DateSkill
from skills.open_app import OpenAppSkill
from skills.remember import RememberSkill
from skills.recall import RecallSkill
from skills.forget import ForgetSkill
from skills.open_path import OpenPathSkill
from skills.create_path import CreatePathSkill
from skills.rename_path import RenamePathSkill
from skills.move_path import MovePathSkill
from skills.delete_path import DeletePathSkill
from skills.find_file import FindFileSkill
from skills.search_files import SearchFilesSkill
from skills.open_search_result import OpenSearchResultSkill
from skills.web_search import WebSearchSkill
from skills.get_weather import GetWeatherSkill
from skills.get_news import GetNewsSkill
from skills.open_url import OpenUrlSkill
from skills.clipboard import ClipboardReadSkill, ClipboardWriteSkill
from skills.window_control import FocusWindowSkill, MinimizeWindowSkill
from skills.volume_control import VolumeControlSkill
from skills.process_control import ListProcessesSkill, CloseAppSkill
from skills.workflow import SaveWorkflowSkill, RunWorkflowSkill
from skills.trigger import SetTriggerSkill, ListTriggersSkill, DeleteTriggerSkill
from skills.reminder import SetReminderSkill, ListRemindersSkill
from skills.screenshot import TakeScreenshotSkill
from skills.read_screen import ReadScreenSkill
from skills.describe_screen import DescribeScreenSkill
from skills.notes import AddNoteSkill, ListNotesSkill
from skills.browser_history import GetBrowserHistorySkill
from skills.active_window import GetActiveWindowSkill
from skills.mouse_control import ClickMouseSkill, MoveMouseSkill
from skills.keyboard_control import TypeTextSkill, PressKeySkill
from skills.research import ResearchSkill
from skills.semantic_search_files import SemanticSearchFilesSkill
from skills.build_semantic_index import BuildSemanticIndexSkill
from skills.todo import AddTodoSkill, ListTodosSkill, CompleteTodoSkill
from skills.system_power import SystemPowerSkill
from skills.brightness_control import SetBrightnessSkill
from skills.recycle_bin import EmptyRecycleBinSkill
from skills.wifi_control import ListWifiNetworksSkill
from skills.git_control import GitStatusSkill, GitPullSkill
from skills.editor_control import OpenInEditorSkill
from skills.run_command import RunCommandSkill
from skills.media_control import MediaControlSkill
from skills.fun import TellJokeSkill, RollDiceSkill, FlipCoinSkill
from skills.ask_question import AskQuestionSkill
from skills.translate_text import TranslateTextSkill
from skills.calculate import CalculateSkill
from skills.convert_units import ConvertUnitsSkill
from skills.summarize_clipboard import SummarizeClipboardSkill
from skills.system_info import (
    GetBatteryStatusSkill, GetCpuUsageSkill, GetMemoryUsageSkill, GetDiskUsageSkill,
    GetUptimeSkill, GetSystemInfoSkill, GetLocalIpSkill, GetPublicIpSkill,
)
from skills.system_maintenance import (
    ClearTempFilesSkill, RestartExplorerSkill, FlushDnsSkill, ListStartupAppsSkill,
    ListInstalledAppsSkill, SetPowerPlanSkill, ToggleDarkModeSkill, GetWifiStatusSkill,
)
from skills.network_utils import PingHostSkill, TraceRouteSkill, CheckWebsiteStatusSkill
from skills.file_utils import (
    CompressPathSkill, ExtractArchiveSkill, GetFileInfoSkill, CountWordsInFileSkill,
    ReadFileTextSkill, DuplicateFileSkill, GetFolderSizeSkill,
)
from skills.window_layout import (
    ListOpenWindowsSkill, MaximizeWindowSkill, RestoreWindowSkill, SnapWindowLeftSkill,
    SnapWindowRightSkill, SwitchNextWindowSkill, MinimizeAllWindowsSkill,
    SetWindowAlwaysOnTopSkill, ResizeWindowSkill,
)
from skills.reminders_extra import (
    SnoozeReminderSkill, DeleteReminderSkill, SetDailyReminderSkill, StartPomodoroSkill, StopPomodoroSkill,
)
from skills.todo import DeleteTodoSkill
from skills.notes import SearchNotesSkill, ExportNotesSkill, ClearNotesSkill
from skills.git_control import GitLogSkill, GitBranchSkill, GitDiffSkill
from skills.dev_tools import (
    RunPythonScriptSkill, FormatJsonSkill, CountLinesOfCodeSkill, GenerateUuidSkill,
    CheckPortInUseSkill, KillProcessByPortSkill,
)
from skills.fun2 import RandomQuoteSkill, RandomFactSkill, Magic8BallSkill, ChooseRandomSkill, RockPaperScissorsSkill
from skills.text_utils import (
    CountWordsSkill, ConvertCaseSkill, GeneratePasswordSkill, ProofreadTextSkill,
    SummarizeTextSkill, DetectLanguageSkill, ExtractUrlsFromTextSkill,
    ConvertNumberToWordsSkill, ConvertRomanNumeralSkill, ConvertMorseCodeSkill,
)
from skills.datetime_utils import GetDayOfWeekSkill, DaysUntilSkill, GetWeekNumberSkill, ConvertTimezoneSkill
from skills.print_utils import PrintFileSkill
from skills.personal_utils import CalculateBmiSkill, CalculateTipSkill, CalculateAgeSkill, CalculateDiscountSkill
from skills.clipboard import TranslateClipboardSkill
from skills.app_utils import ListRecentFilesSkill, OpenIncognitoWindowSkill, EmptyClipboardSkill
from skills.math_utils2 import IsPrimeSkill, FibonacciSkill, GcdLcmSkill, CalculatePercentageSkill, RandomNumberSkill
from skills.security_utils import CheckPasswordStrengthSkill, CheckFileHashSkill
from skills.astronomy import GetSunriseSunsetSkill, GetMoonPhaseSkill
from skills.currency import ConvertCurrencySkill
from skills.holidays import GetNextHolidaySkill
from skills.system_info2 import (
    GetScreenResolutionSkill, GetGpuInfoSkill, GetDnsServersSkill,
    GetEnvironmentVariableSkill, GetMacAddressSkill, ListDrivesSkill,
)
from skills.file_utils2 import FindLargeFilesSkill, FindDuplicateFilesSkill
from skills.browser_search import SearchInBrowserSkill, PlayMediaSkill
from skills.timer import SetTimerSkill, CancelTimerSkill, ListTimersSkill
from skills.screen_click import ClickTextSkill, ClickElementSkill, ScrollSkill
from skills.volume_level import SetVolumeLevelSkill, GetVolumeLevelSkill
from skills.read_selection import ReadSelectionSkill
from skills.close_window import CloseWindowSkill
from skills.chitchat import ChitChatSkill
from skills.contacts import ContactBook, SaveContactSkill, ListContactsSkill, SendWhatsAppSkill, SendEmailSkill
from core.ollama_client import OllamaClient
from core.path_resolver import resolve_user_path
from core.memory_manager import MemoryManager
from core.conversation_state import ConversationStateManager
from core.nest_client import NestClient
from core.config import Config
from core.embedding_provider import EmbeddingProvider
from core.vision_provider import VisionProvider
from core.planner_provider import PlannerProvider
from core.plan_executor import PlanExecutor
from core.workflow_manager import WorkflowManager
from core.trigger_manager import TriggerManager
from core.reminder_manager import ReminderManager
from core.todo_manager import TodoManager
from copy import deepcopy


class SkillRegistry:

    def __init__(
        self,
        memory_manager: MemoryManager = None,
        conversation_state: ConversationStateManager = None,
        nest_client: NestClient = None,
        config: Config = None,
        embedding_provider: EmbeddingProvider = None,
        reminder_manager: ReminderManager = None,
    ):
        self.memory_manager = memory_manager or MemoryManager()
        self.conversation_state = conversation_state or ConversationStateManager()
        self.nest_client = nest_client or NestClient()
        self.config = config or Config()
        self.embedding_provider = embedding_provider or EmbeddingProvider(
            model=self.config.get("embedding_model", "nomic-embed-text")
        )
        self.vision_provider = VisionProvider(model=self.config.get("vision_model", "qwen2.5vl:7b"))
        self.planner_provider = PlannerProvider(self, model=self.config.get("ollama_model", "qwen2.5:7b"))
        self.plan_executor = PlanExecutor(self)
        self.workflow_manager = WorkflowManager(self.memory_manager)
        self.trigger_manager = TriggerManager(self.memory_manager, self.workflow_manager)
        self.reminder_manager = reminder_manager or ReminderManager()
        self.todo_manager = TodoManager()

        # Client Ollama condiviso (v3.0: 127.0.0.1, keep_alive lungo) e rubrica.
        self.ollama_client = OllamaClient()
        self.contact_book = ContactBook(self.memory_manager)
        model = self.config.get("ollama_model", "qwen2.5:7b")

        # Condivise anche col research agent (v1.5), che le orchestra invece di limitarsi a
        # incatenarle come fa il Planner generico.
        web_search_skill = WebSearchSkill()
        search_files_skill = SearchFilesSkill(self.nest_client, self.conversation_state)

        self.skills = {
            "GET_TIME": TimeSkill(),
            "GET_DATE": DateSkill(),
            "OPEN_APP": OpenAppSkill(),
            "REMEMBER": RememberSkill(self.memory_manager, self.embedding_provider),
            "RECALL": RecallSkill(self.memory_manager, self.embedding_provider),
            "FORGET": ForgetSkill(self.memory_manager),
            "OPEN_PATH": OpenPathSkill(),
            "CREATE_PATH": CreatePathSkill(),
            "RENAME_PATH": RenamePathSkill(),
            "MOVE_PATH": MovePathSkill(),
            "DELETE_PATH": DeletePathSkill(),
            "FIND_FILE": FindFileSkill(),
            "SEARCH_FILES": search_files_skill,
            "OPEN_SEARCH_RESULT": OpenSearchResultSkill(self.conversation_state),
            "WEB_SEARCH": web_search_skill,
            "GET_WEATHER": GetWeatherSkill(self.config),
            "GET_NEWS": GetNewsSkill(self.config),
            "OPEN_URL": OpenUrlSkill(),
            "CLIPBOARD_READ": ClipboardReadSkill(),
            "CLIPBOARD_WRITE": ClipboardWriteSkill(),
            "FOCUS_WINDOW": FocusWindowSkill(),
            "MINIMIZE_WINDOW": MinimizeWindowSkill(),
            "SET_VOLUME": VolumeControlSkill(),
            "LIST_PROCESSES": ListProcessesSkill(),
            "CLOSE_APP": CloseAppSkill(),
            "SAVE_WORKFLOW": SaveWorkflowSkill(self.planner_provider, self.workflow_manager),
            "RUN_WORKFLOW": RunWorkflowSkill(self.workflow_manager, self.plan_executor),
            "SET_TRIGGER": SetTriggerSkill(self.trigger_manager),
            "LIST_TRIGGERS": ListTriggersSkill(self.trigger_manager),
            "DELETE_TRIGGER": DeleteTriggerSkill(self.trigger_manager),
            "SET_REMINDER": SetReminderSkill(self.reminder_manager),
            "LIST_REMINDERS": ListRemindersSkill(self.reminder_manager),
            "TAKE_SCREENSHOT": TakeScreenshotSkill(),
            "READ_SCREEN": ReadScreenSkill(),
            "DESCRIBE_SCREEN": DescribeScreenSkill(self.vision_provider),
            "ADD_NOTE": AddNoteSkill(),
            "LIST_NOTES": ListNotesSkill(),
            "GET_BROWSER_HISTORY": GetBrowserHistorySkill(),
            "GET_ACTIVE_WINDOW": GetActiveWindowSkill(),
            "CLICK_MOUSE": ClickMouseSkill(),
            "MOVE_MOUSE": MoveMouseSkill(),
            "TYPE_TEXT": TypeTextSkill(),
            "PRESS_KEY": PressKeySkill(),
            "RESEARCH": ResearchSkill(
                web_search_skill, search_files_skill, model=self.config.get("ollama_model", "qwen2.5:7b")
            ),
            "SEMANTIC_SEARCH_FILES": SemanticSearchFilesSkill(self.nest_client, self.conversation_state),
            "BUILD_SEMANTIC_INDEX": BuildSemanticIndexSkill(self.nest_client),
            "ADD_TODO": AddTodoSkill(self.todo_manager),
            "LIST_TODOS": ListTodosSkill(self.todo_manager),
            "COMPLETE_TODO": CompleteTodoSkill(self.todo_manager),
            "SYSTEM_POWER": SystemPowerSkill(),
            "SET_BRIGHTNESS": SetBrightnessSkill(),
            "EMPTY_RECYCLE_BIN": EmptyRecycleBinSkill(),
            "LIST_WIFI_NETWORKS": ListWifiNetworksSkill(),
            "GIT_STATUS": GitStatusSkill(),
            "GIT_PULL": GitPullSkill(),
            "OPEN_IN_EDITOR": OpenInEditorSkill(),
            "RUN_COMMAND": RunCommandSkill(),
            "MEDIA_CONTROL": MediaControlSkill(),
            "TELL_JOKE": TellJokeSkill(),
            "ROLL_DICE": RollDiceSkill(),
            "FLIP_COIN": FlipCoinSkill(),
            "ASK_QUESTION": AskQuestionSkill(
                self.conversation_state, model=self.config.get("ollama_model", "qwen2.5:7b")
            ),
            "TRANSLATE_TEXT": TranslateTextSkill(model=self.config.get("ollama_model", "qwen2.5:7b")),
            "CALCULATE": CalculateSkill(),
            "CONVERT_UNITS": ConvertUnitsSkill(),
            "SUMMARIZE_CLIPBOARD": SummarizeClipboardSkill(model=self.config.get("ollama_model", "qwen2.5:7b")),
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
            "GET_WIFI_STATUS": GetWifiStatusSkill(),
            "PING_HOST": PingHostSkill(),
            "TRACE_ROUTE": TraceRouteSkill(),
            "CHECK_WEBSITE_STATUS": CheckWebsiteStatusSkill(),
            "COMPRESS_PATH": CompressPathSkill(),
            "EXTRACT_ARCHIVE": ExtractArchiveSkill(),
            "GET_FILE_INFO": GetFileInfoSkill(),
            "COUNT_WORDS_IN_FILE": CountWordsInFileSkill(),
            "READ_FILE_TEXT": ReadFileTextSkill(),
            "DUPLICATE_FILE": DuplicateFileSkill(),
            "GET_FOLDER_SIZE": GetFolderSizeSkill(),
            "LIST_OPEN_WINDOWS": ListOpenWindowsSkill(),
            "MAXIMIZE_WINDOW": MaximizeWindowSkill(),
            "RESTORE_WINDOW": RestoreWindowSkill(),
            "SNAP_WINDOW_LEFT": SnapWindowLeftSkill(),
            "SNAP_WINDOW_RIGHT": SnapWindowRightSkill(),
            "SWITCH_NEXT_WINDOW": SwitchNextWindowSkill(),
            "MINIMIZE_ALL_WINDOWS": MinimizeAllWindowsSkill(),
            "SET_WINDOW_ALWAYS_ON_TOP": SetWindowAlwaysOnTopSkill(),
            "RESIZE_WINDOW": ResizeWindowSkill(),
            "SNOOZE_REMINDER": SnoozeReminderSkill(self.reminder_manager),
            "DELETE_REMINDER": DeleteReminderSkill(self.reminder_manager),
            "SET_DAILY_REMINDER": SetDailyReminderSkill(self.reminder_manager),
            "START_POMODORO": StartPomodoroSkill(self.reminder_manager),
            "STOP_POMODORO": StopPomodoroSkill(self.reminder_manager),
            "DELETE_TODO": DeleteTodoSkill(self.todo_manager),
            "SEARCH_NOTES": SearchNotesSkill(),
            "EXPORT_NOTES": ExportNotesSkill(),
            "CLEAR_NOTES": ClearNotesSkill(),
            "GIT_LOG": GitLogSkill(),
            "GIT_BRANCH": GitBranchSkill(),
            "GIT_DIFF": GitDiffSkill(),
            "RUN_PYTHON_SCRIPT": RunPythonScriptSkill(),
            "FORMAT_JSON": FormatJsonSkill(),
            "COUNT_LINES_OF_CODE": CountLinesOfCodeSkill(),
            "GENERATE_UUID": GenerateUuidSkill(),
            "CHECK_PORT_IN_USE": CheckPortInUseSkill(),
            "KILL_PROCESS_BY_PORT": KillProcessByPortSkill(),
            "RANDOM_QUOTE": RandomQuoteSkill(),
            "RANDOM_FACT": RandomFactSkill(),
            "MAGIC_8_BALL": Magic8BallSkill(),
            "CHOOSE_RANDOM": ChooseRandomSkill(),
            "ROCK_PAPER_SCISSORS": RockPaperScissorsSkill(),
            "COUNT_WORDS": CountWordsSkill(),
            "CONVERT_CASE": ConvertCaseSkill(),
            "GENERATE_PASSWORD": GeneratePasswordSkill(),
            "PROOFREAD_TEXT": ProofreadTextSkill(model=self.config.get("ollama_model", "qwen2.5:7b")),
            "SUMMARIZE_TEXT": SummarizeTextSkill(model=self.config.get("ollama_model", "qwen2.5:7b")),
            "DETECT_LANGUAGE": DetectLanguageSkill(model=self.config.get("ollama_model", "qwen2.5:7b")),
            "EXTRACT_URLS_FROM_TEXT": ExtractUrlsFromTextSkill(),
            "CONVERT_NUMBER_TO_WORDS": ConvertNumberToWordsSkill(),
            "CONVERT_ROMAN_NUMERAL": ConvertRomanNumeralSkill(),
            "CONVERT_MORSE_CODE": ConvertMorseCodeSkill(),
            "GET_DAY_OF_WEEK": GetDayOfWeekSkill(),
            "DAYS_UNTIL": DaysUntilSkill(),
            "GET_WEEK_NUMBER": GetWeekNumberSkill(),
            "CONVERT_TIMEZONE": ConvertTimezoneSkill(),
            "PRINT_FILE": PrintFileSkill(),
            "CALCULATE_BMI": CalculateBmiSkill(),
            "CALCULATE_TIP": CalculateTipSkill(),
            "CALCULATE_AGE": CalculateAgeSkill(),
            "CALCULATE_DISCOUNT": CalculateDiscountSkill(),
            "TRANSLATE_CLIPBOARD": TranslateClipboardSkill(model=self.config.get("ollama_model", "qwen2.5:7b")),
            "LIST_RECENT_FILES": ListRecentFilesSkill(),
            "OPEN_INCOGNITO_WINDOW": OpenIncognitoWindowSkill(),
            "EMPTY_CLIPBOARD": EmptyClipboardSkill(),
            "IS_PRIME": IsPrimeSkill(),
            "FIBONACCI": FibonacciSkill(),
            "GCD_LCM": GcdLcmSkill(),
            "CALCULATE_PERCENTAGE": CalculatePercentageSkill(),
            "RANDOM_NUMBER": RandomNumberSkill(),
            "CHECK_PASSWORD_STRENGTH": CheckPasswordStrengthSkill(),
            "CHECK_FILE_HASH": CheckFileHashSkill(),
            "GET_SUNRISE_SUNSET": GetSunriseSunsetSkill(),
            "GET_MOON_PHASE": GetMoonPhaseSkill(),
            "CONVERT_CURRENCY": ConvertCurrencySkill(),
            "GET_NEXT_HOLIDAY": GetNextHolidaySkill(),
            "GET_SCREEN_RESOLUTION": GetScreenResolutionSkill(),
            "GET_GPU_INFO": GetGpuInfoSkill(),
            "GET_DNS_SERVERS": GetDnsServersSkill(),
            "GET_ENVIRONMENT_VARIABLE": GetEnvironmentVariableSkill(),
            "GET_MAC_ADDRESS": GetMacAddressSkill(),
            "LIST_DRIVES": ListDrivesSkill(),
            "FIND_LARGE_FILES": FindLargeFilesSkill(),
            "FIND_DUPLICATE_FILES": FindDuplicateFilesSkill(),
            # ---- v3.0: agire davvero al posto dell'utente ----
            "SEARCH_IN_BROWSER": SearchInBrowserSkill(),
            "PLAY_MEDIA": PlayMediaSkill(),
            "SET_TIMER": SetTimerSkill(self.reminder_manager),
            "CANCEL_TIMER": CancelTimerSkill(self.reminder_manager),
            "LIST_TIMERS": ListTimersSkill(self.reminder_manager),
            "CLICK_TEXT": ClickTextSkill(),
            "CLICK_ELEMENT": ClickElementSkill(self.vision_provider),
            "SCROLL": ScrollSkill(),
            "SET_VOLUME_LEVEL": SetVolumeLevelSkill(),
            "GET_VOLUME_LEVEL": GetVolumeLevelSkill(),
            "READ_SELECTION": ReadSelectionSkill(),
            "CLOSE_WINDOW": CloseWindowSkill(),
            "CHITCHAT": ChitChatSkill(self.ollama_client, model=model),
            "SAVE_CONTACT": SaveContactSkill(self.contact_book),
            "LIST_CONTACTS": ListContactsSkill(self.contact_book),
            "SEND_WHATSAPP": SendWhatsAppSkill(self.contact_book),
            "SEND_EMAIL": SendEmailSkill(self.contact_book),
        }

    def get_skill(self, intent: str):
        return self.skills.get(intent, None)

    def has_skill(self, intent: str) -> bool:
        return intent in self.skills

    def register_skill(self, intent: str, skill) -> None:
        """Registra una skill aggiuntiva a runtime: il punto di estensione per plugin di terze
        parti, senza dover modificare l'elenco hardcoded in __init__. La skill deve esporre
        un attributo 'metadata' (intent/description/parameters) e un metodo execute(parameters)."""
        self.skills[intent] = skill

    def is_remote(self, intent: str) -> bool:
        """Vero se la skill richiede rete/servizi esterni (distinzione tool locali/remoti)."""
        skill = self.get_skill(intent)
        return bool(getattr(skill, "metadata", {}).get("remote", False))

    def list_capabilities(self) -> list[dict]:
        """Restituisce i metadata delle skill registrate."""
        capabilities = []
        for intent, skill in self.skills.items():
            metadata = deepcopy(getattr(skill, "metadata", {}))
            metadata.setdefault("intent", intent)
            metadata.setdefault("description", "")
            metadata.setdefault("parameters", {})
            capabilities.append(metadata)
        return capabilities

    PATH_PARAMETERS = ("path", "destination")

    def unregister_skill(self, intent: str) -> bool:
        return self.skills.pop(intent, None) is not None

    def app_names(self) -> list[str]:
        """Nomi delle app installate (v3.0: vocabolario per correggere le trascrizioni vocali)."""
        skill = self.get_skill("OPEN_APP")
        resolver = getattr(skill, "app_resolver", None)
        try:
            return resolver.display_names() if resolver is not None else []
        except Exception:
            return []

    def execute(self, intent: str, parameters: dict = None):
        skill = self.get_skill(intent)
        if skill is None:
            return None

        # Percorsi "parlati" (v3.0): "desktop\note.txt", "download" -> percorso reale.
        if parameters:
            for name in self.PATH_PARAMETERS:
                value = parameters.get(name)
                if isinstance(value, str) and value.strip():
                    resolved = resolve_user_path(value, prefer_existing=intent != "CREATE_PATH")
                    if resolved != value:
                        parameters = dict(parameters)
                        parameters[name] = resolved

        return skill.execute(parameters)
