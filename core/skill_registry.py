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
from skills.reminder import SetReminderSkill, ListRemindersSkill
from skills.screenshot import TakeScreenshotSkill
from skills.read_screen import ReadScreenSkill
from skills.active_window import GetActiveWindowSkill
from skills.mouse_control import ClickMouseSkill, MoveMouseSkill
from skills.keyboard_control import TypeTextSkill, PressKeySkill
from skills.research import ResearchSkill
from skills.semantic_search_files import SemanticSearchFilesSkill
from skills.build_semantic_index import BuildSemanticIndexSkill
from core.memory_manager import MemoryManager
from core.conversation_state import ConversationStateManager
from core.nest_client import NestClient
from core.config import Config
from core.embedding_provider import EmbeddingProvider
from core.planner_provider import PlannerProvider
from core.plan_executor import PlanExecutor
from core.workflow_manager import WorkflowManager
from core.reminder_manager import ReminderManager
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
        self.planner_provider = PlannerProvider(self, model=self.config.get("ollama_model", "qwen2.5:7b"))
        self.plan_executor = PlanExecutor(self)
        self.workflow_manager = WorkflowManager(self.memory_manager)
        self.reminder_manager = reminder_manager or ReminderManager()

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
            "SET_REMINDER": SetReminderSkill(self.reminder_manager),
            "LIST_REMINDERS": ListRemindersSkill(self.reminder_manager),
            "TAKE_SCREENSHOT": TakeScreenshotSkill(),
            "READ_SCREEN": ReadScreenSkill(),
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

    def execute(self, intent: str, parameters: dict = None):
        skill = self.get_skill(intent)
        if skill is None:
            return None

        return skill.execute(parameters)
