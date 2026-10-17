"""In-memory findings shared by the Clean status, review list and copy gate."""
from difflib import SequenceMatcher, get_close_matches
import re


def review_findings(report, audit, *, source='', output=''):
    items = []
    if report:
        for loss in report.losses:
            if loss.category == 'by_design':
                continue
            items.append(dict(severity='blocked' if loss.category == 'unexpected' else 'review',
                              title=f'Clinical value removed: {loss.display}',
                              detail=loss.stage_label, before=loss.lines[0] if loss.lines else '', after=''))
        for change in report.meaning:
            items.append(dict(severity='blocked' if change.category == 'unexpected' else 'review',
                              title=change.message, detail=change.stage_label,
                              before=change.before, after=change.after))
        for value in report.introduced:
            items.append(dict(severity='blocked' if value.category == 'unexpected' else 'review',
                              title=f'Value introduced: {value.display}', detail=value.stage_label,
                              before='', after=value.lines[0] if value.lines else ''))
        for value in report.implausible:
            items.append(dict(severity='review' if value.key() in report.implausible_in_source else 'blocked',
                              title=value.message, detail='Check the source and units',
                              before=value.line if value.key() in report.implausible_in_source else '', after=value.line))
    if audit and not audit.skipped:
        for finding in audit.findings:
            lines = output.splitlines()
            actual = lines[finding.line - 1] if 0 < finding.line <= len(lines) else ''
            original = actual if actual and actual in source else ''
            if actual and not original:
                nearby = get_close_matches(actual, source.splitlines(), n=1, cutoff=0.6)
                original = nearby[0] if nearby else ''
            items.append(dict(severity='review', title='Possible identifying information',
                              detail=finding.check, before=original, after=actual))
    return sorted(items, key=lambda item: item['severity'] != 'blocked')


def review_status(report, audit):
    items = review_findings(report, audit)
    if (report and report.status == 'alert') or any(x['severity'] == 'blocked' for x in items):
        return 'blocked'
    if items or report is None or audit is None or audit.skipped or audit.errors:
        return 'review'
    return 'clean'


def redaction_candidates(before, after):
    """Map only unambiguous single-token, single-line replacements for inspection.

    Ambiguous changes deliberately have no original candidate; users can still
    inspect/edit them without guessing which source text should be restored.
    """
    matches = list(re.finditer(r'\[REDACTED_[A-Z_]+\]', after))
    mapping = {}
    source_lines = before.splitlines()
    indexed_output = [(i, line) for i, line in enumerate(after.splitlines())
                      if line not in ('<patient_chart>', '</patient_chart>')]
    output_lines = [line for _, line in indexed_output]
    opcodes = SequenceMatcher(None, source_lines, output_lines, autojunk=False).get_opcodes()
    for tag, a, b, c, d in opcodes:
        if tag != 'replace' or b-a != d-c:
            continue
        for line_index, (source, output) in enumerate(zip(source_lines[a:b], output_lines[c:d]), c):
            tokens = list(re.finditer(r'\[REDACTED_[A-Z_]+\]', output))
            if len(tokens) != 1:
                continue
            token = tokens[0]
            prefix, suffix = output[:token.start()], output[token.end():]
            if source.startswith(prefix) and source.endswith(suffix):
                original = source[len(prefix):len(source)-len(suffix) if suffix else len(source)]
                mapping[indexed_output[line_index][0]] = original
    return [dict(start=m.start(), end=m.end(), token=m.group(),
                 original=mapping.get(after[:m.start()].count('\n')))
            for m in matches]
