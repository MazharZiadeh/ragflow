**Context**:
 - Goal: {{ goal }}
 - Tool calls executed:
{% for call in tool_calls %}
Tool: `{{ call.name }}`
Results: {{ call.result }}
{% endfor %}

## Reflect briefly on the tool call results.

Answer these questions concisely (1-2 sentences each, skip any that don't apply):

1. **Relevance**: Do the results directly answer the goal? If not, what's missing?
2. **Sufficiency**: Is there enough information to proceed or answer, or do we need another tool call?
3. **Accuracy**: Any contradictions or unreliable data in the results?
4. **Next step**: What is the single best next action? If the goal is answered, say so.

Keep your total reflection under 100 words. Do NOT repeat or summarize the tool results — focus only on your assessment.
