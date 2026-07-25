"""Diagnosis tools package — registers all analysis tools."""

from routebench.analysis.diagnosis.compliance import ComplianceAnalysis
from routebench.analysis.diagnosis.dispatch import DispatchAnalysis
from routebench.analysis.diagnosis.outliers import OutlierAnalysis
from routebench.analysis.diagnosis.reachability import ReachabilityAnalysis
from routebench.analysis.diagnosis.sequencing import SequencingAnalysis
from routebench.analysis.diagnosis.territory import TerritoryAnalysis
from routebench.analysis.diagnosis.time_pressure import TimePressureAnalysis
from routebench.analysis.tools import register_tool

register_tool(SequencingAnalysis())
register_tool(TimePressureAnalysis())
register_tool(OutlierAnalysis())
register_tool(TerritoryAnalysis())
register_tool(DispatchAnalysis())
register_tool(ComplianceAnalysis())
register_tool(ReachabilityAnalysis())
