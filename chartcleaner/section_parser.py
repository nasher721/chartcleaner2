"""Clinical section parser and structured LLM format transformer.

Transforms unstructured EMR text into canonical clinical domains:
- Chief Complaint / Reason for Visit
- History of Present Illness (HPI)
- Past Medical & Surgical History (PMH/PSH)
- Active Medications
- Allergies
- Vitals & Physical Exam
- Labs & Diagnostic Studies
- Imaging
- Assessment & Plan (A/P)

Supports serialization to:
1. Canonical Markdown with standard headings
2. Prompt-Optimized XML for LLMs (<patient_chart>, <medications>, <plan>)
3. Standardized JSON / Dict for RAG and agent pipelines.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any

__all__ = ["ClinicalSection", "ParsedChart", "parse_clinical_sections"]

# Section taxonomy mapping: canonical domain key -> regex aliases
SECTION_TAXONOMY: dict[str, list[str]] = {
    "chief_complaint": [
        r"Chief\s+Complaint", r"Reason\s+for\s+Visit", r"CC",
    ],
    "history_of_present_illness": [
        r"History\s+of\s+Present\s+Illness", r"HPI", r"Subjective",
    ],
    "past_medical_history": [
        r"Past\s+Medical\s+History", r"PMH", r"Past\s+Surgical\s+History", r"PSH",
        r"Medical\s+History", r"Surgical\s+History",
    ],
    "medications": [
        r"Current\s+Medications?", r"Active\s+Medications?", r"Home\s+Medications?",
        r"Medications?", r"Med\s+List",
    ],
    "allergies": [
        r"Allergies\s+and\s+Adverse\s+Reactions?", r"Allergies\s*/\s*Sensitivities",
        r"Allergies",
    ],
    "vitals": [
        r"Vital\s+Signs", r"Vitals?",
    ],
    "physical_exam": [
        r"Physical\s+Exam(?:ination)?", r"PE", r"Objective",
    ],
    "labs": [
        r"Laboratory\s+Data", r"Diagnostic\s+Labs?", r"Recent\s+Labs?", r"Labs?",
    ],
    "imaging": [
        r"Diagnostic\s+Imaging", r"Radiology", r"Imaging", r"Studies",
    ],
    "hospital_course": [
        r"Brief\s+Hospital\s+Course", r"Clinical\s+Course", r"Hospital\s+Course",
    ],
    "assessment_and_plan": [
        r"Assessment\s+(?:&|and)\s+Plan", r"A\s*/\s*P", r"Assessment", r"Plan", r"Impression",
    ],
}


@dataclass
class ClinicalSection:
    domain: str            # Canonical domain key e.g. "medications"
    title: str             # Cleaned header title e.g. "Chief Complaint"
    content: str           # Body text of the section
    start_pos: int
    end_pos: int


@dataclass
class ParsedChart:
    preamble: str = ""
    sections: list[ClinicalSection] = field(default_factory=list)
    metadata: dict[str, str] = field(default_factory=dict)

    def get_section(self, domain: str) -> ClinicalSection | None:
        for s in self.sections:
            if s.domain == domain:
                return s
        return None

    def to_dict(self) -> dict[str, Any]:
        """Return a structured dictionary representation."""
        domains: dict[str, str] = {}
        for s in self.sections:
            if s.domain not in domains:
                domains[s.domain] = s.content.strip()
            else:
                domains[s.domain] += "\n\n" + s.content.strip()

        return {
            "metadata": self.metadata,
            "preamble": self.preamble.strip(),
            "domains": domains,
            "sections": [asdict(s) for s in self.sections],
        }

    def to_json(self, indent: int = 2) -> str:
        """Export to JSON string for APIs or RAG vector storage."""
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)

    def to_markdown(self) -> str:
        """Export as clean, hierarchical Markdown."""
        lines: list[str] = []
        if self.preamble.strip():
            lines.append(self.preamble.strip())
            lines.append("")

        for s in self.sections:
            title = s.title.strip().rstrip(":")
            lines.append(f"## {title}")
            lines.append(s.content.strip())
            lines.append("")

        return "\n".join(lines).strip()

    def to_llm_xml(self) -> str:
        """Export as token-dense XML optimized for LLM prompt engineering."""
        lines: list[str] = ["<patient_chart>"]

        if self.preamble.strip():
            lines.append(f"  <context>\n    {self.preamble.strip()}\n  </context>")

        domain_tags = {
            "chief_complaint": "chief_complaint",
            "history_of_present_illness": "hpi",
            "past_medical_history": "medical_history",
            "medications": "active_medications",
            "allergies": "allergies",
            "vitals": "vitals",
            "physical_exam": "physical_exam",
            "labs": "laboratory_results",
            "imaging": "imaging",
            "hospital_course": "hospital_course",
            "assessment_and_plan": "assessment_and_plan",
        }

        for s in self.sections:
            tag = domain_tags.get(s.domain, s.domain)
            body = s.content.strip()
            lines.append(f"  <{tag}>\n    {body}\n  </{tag}>")

        lines.append("</patient_chart>")
        return "\n".join(lines)


def _build_combined_regex() -> tuple[re.Pattern, dict[str, str]]:
    pattern_parts = []
    group_to_domain = {}
    idx = 1
    for domain, aliases in SECTION_TAXONOMY.items():
        for alias in aliases:
            grp_name = f"sec_{idx}"
            # Matches header at start of line, optional markdown hash/bold, word boundaries, and line end or colon
            pattern_parts.append(
                rf"(?P<{grp_name}>^[ \t]*(?:#+[ \t]*|\*\*)?(?:\b{alias}\b)(?:\*\*)?[ \t]*(?::[ \t]*$|:\s+[^\n]+$|$))"
            )
            group_to_domain[grp_name] = domain
            idx += 1
    combined = re.compile("|".join(pattern_parts), re.IGNORECASE | re.MULTILINE)
    return combined, group_to_domain


_COMBINED_SECTION_RE, _GROUP_TO_DOMAIN = _build_combined_regex()


def parse_clinical_sections(text: str) -> ParsedChart:
    """Parse text into canonical clinical sections and metadata."""
    matches = list(_COMBINED_SECTION_RE.finditer(text))
    if not matches:
        return ParsedChart(preamble=text, sections=[])

    preamble = text[:matches[0].start()].strip()
    sections: list[ClinicalSection] = []

    for i, match in enumerate(matches):
        domain = "other"
        for grp_name, dom in _GROUP_TO_DOMAIN.items():
            if match.group(grp_name):
                domain = dom
                break

        header_span_start = match.start()
        header_text = match.group(0)

        # Content of this section runs from end of this match until the start of the next section
        end_pos = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        chunk = text[header_span_start:end_pos]

        # First line is the header itself (and any inline content)
        if "\n" in chunk:
            first_line, rest = chunk.split("\n", 1)
        else:
            first_line, rest = chunk, ""

        # Extract title and inline content
        # e.g. "Chief Complaint: Exertional shortness of breath"
        if ":" in first_line:
            title_raw, inline_raw = first_line.split(":", 1)
            title = title_raw.strip().lstrip("#").strip("*").strip()
            inline = inline_raw.strip()
        else:
            title = first_line.strip().lstrip("#").strip("*").strip()
            inline = ""

        body_parts = [inline, rest] if inline else [rest]
        content = "\n".join(p for p in body_parts if p).strip()

        sections.append(
            ClinicalSection(
                domain=domain,
                title=title,
                content=content,
                start_pos=header_span_start,
                end_pos=end_pos,
            )
        )

    return ParsedChart(preamble=preamble, sections=sections)
