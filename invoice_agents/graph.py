from langgraph.graph import END, START, StateGraph

from invoice_agents.agents.approval import approve
from invoice_agents.agents.ingestion import ingest
from invoice_agents.agents.payment import pay
from invoice_agents.agents.validation import validate
from invoice_agents.models import PipelineState


def build_graph():
    graph = StateGraph(PipelineState)
    graph.add_node("ingestion", ingest)
    graph.add_node("validation", validate)
    graph.add_node("approval", approve)
    graph.add_node("payment", pay)
    graph.add_edge(START, "ingestion")
    graph.add_edge("ingestion", "validation")
    graph.add_edge("validation", "approval")
    graph.add_edge("approval", "payment")
    graph.add_edge("payment", END)
    return graph.compile()
