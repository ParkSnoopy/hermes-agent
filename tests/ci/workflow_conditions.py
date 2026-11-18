"""Evaluate the subset of Actions conditions used by workflow contract tests."""
import ast
import re


def evaluate(expression, inputs, needs, *, cancelled=False, failed=False, job_if=True, github=None, env=None,
             steps=None):
    """Evaluate an expression, including Actions' implicit success condition.

    This is not a scheduler simulation; Actions owns cancellation and skipped-ancestor
    propagation. A step condition judges implicit success by earlier step failure.
    """
    expression = str(expression).strip().removeprefix('${{').removesuffix('}}').strip()
    if not re.search(r'\b(always|cancelled|success|failure)\(', expression):
        if cancelled or failed:
            return False
        if job_if and any(row.get('result') != 'success' for row in needs.values()):
            return False

    def value(match):
        bits = match[0].split('.')
        result = {'inputs': inputs, 'needs': needs, 'github': github or {}, 'env': env or {}, 'steps': steps or {}}
        for bit in bits:
            result = result.get(bit, '') if isinstance(result, dict) else ''
        return repr(result)

    expression = re.sub(r'\b(?:inputs|needs|github|env|steps)(?:\.[\w-]+)+', value, expression)
    expression = expression.replace('&&', ' and ').replace('||', ' or ')
    expression = re.sub(r'!(?!=)', ' not ', expression)
    expression = re.sub(r'\btrue\b', 'True', expression)
    expression = re.sub(r'\bfalse\b', 'False', expression)
    return eval('(' + expression.strip() + ')', {'__builtins__': {}}, {
        'always': lambda: True, 'cancelled': lambda: cancelled,
        'success': lambda: not (cancelled or failed), 'failure': lambda: failed,
        'contains': lambda value, needle: needle in value,
        'fromJSON': ast.literal_eval,
    })


def gate(expression, inputs, needs, **options) -> bool:
    return bool(evaluate(expression, inputs, needs, **options))