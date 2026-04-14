"""Guardian agents for each module.

All 10 pipeline modules have a dedicated Guardian Agent.
"""

from core.agent_guardian import register_guardian, get_guardian
from modules.guardians.query_generator_guardian import QueryGeneratorGuardianAgent
from modules.guardians.paper_fetcher_guardian import PaperFetcherGuardianAgent
from modules.guardians.preprocessor_guardian import PreprocessorGuardianAgent
from modules.guardians.frequency_analyzer_guardian import FrequencyAnalyzerGuardianAgent
from modules.guardians.topic_modeler_guardian import TopicModelerGuardianAgent
from modules.guardians.burst_detector_guardian import BurstDetectorGuardianAgent
from modules.guardians.tsr_ranker_guardian import TSRRankerGuardianAgent
from modules.guardians.network_analyzer_guardian import NetworkAnalyzerGuardianAgent
from modules.guardians.visualizer_guardian import VisualizerGuardianAgent
from modules.guardians.report_generator_guardian import ReportGeneratorGuardianAgent

# Register all guardians
register_guardian("query_generator", QueryGeneratorGuardianAgent)
register_guardian("paper_fetcher", PaperFetcherGuardianAgent)
register_guardian("preprocessor", PreprocessorGuardianAgent)
register_guardian("frequency_analyzer", FrequencyAnalyzerGuardianAgent)
register_guardian("topic_modeler", TopicModelerGuardianAgent)
register_guardian("burst_detector", BurstDetectorGuardianAgent)
register_guardian("tsr_ranker", TSRRankerGuardianAgent)
register_guardian("network_analyzer", NetworkAnalyzerGuardianAgent)
register_guardian("visualizer", VisualizerGuardianAgent)
register_guardian("report_generator", ReportGeneratorGuardianAgent)

__all__ = [
    "QueryGeneratorGuardianAgent",
    "PaperFetcherGuardianAgent",
    "PreprocessorGuardianAgent",
    "FrequencyAnalyzerGuardianAgent",
    "TopicModelerGuardianAgent",
    "BurstDetectorGuardianAgent",
    "TSRRankerGuardianAgent",
    "NetworkAnalyzerGuardianAgent",
    "VisualizerGuardianAgent",
    "ReportGeneratorGuardianAgent",
    "get_guardian",
    "register_guardian",
]
