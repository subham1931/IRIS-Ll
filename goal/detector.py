"""
goal/detector.py — Intent and multi-step goal detection for IRIS.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class GoalDetectionResult:
    is_goal: bool
    goal_title: str = ""
    goal_description: str = ""
    requires_planning: bool = False
    confidence: float = 1.0
    estimated_steps: int = 1

    def to_dict(self) -> dict:
        return {
            "is_goal": self.is_goal,
            "goal_title": self.goal_title,
            "goal_description": self.goal_description,
            "requires_planning": self.requires_planning,
            "confidence": self.confidence,
            "estimated_steps": self.estimated_steps,
        }


# Direct single commands that should bypass the goal planner
_SIMPLE_COMMAND_PATTERNS = [
    r"^(open|launch|start)\s+[a-zA-Z0-9_\-\.\s]+$",
    r"^(close|quit|kill)\s+[a-zA-Z0-9_\-\.\s]+$",
    r"^(mute|unmute|volume\s+(up|down|\d+%?)|brightness\s+(up|down|\d+%?))$",
    r"^(turn\s+on|turn\s+off|toggle)\s+(wifi|bluetooth|dark\s+mode)$",
    r"^(undo|revert|cancel\s+that|put\s+it\s+back)$",
    r"^(what\s+time\s+is\s+it|what['’]?s\s+the\s+time|what\s+day\s+is\s+it|what['’]?s\s+the\s+date)$",
    r"^(what['’]?s\s+the\s+weather|weather\s+in\s+[a-zA-Z\s]+)$",
    r"^(take\s+a\s+screenshot|capture\s+screen|look\s+at\s+my\s+screen)$",
    r"^(shutdown|restart|sleep|exit|goodbye)$",
    r"^(hello|hi|hey|how\s+are\s+you|who\s+are\s+you)$",
    r"^(search\s+for|google|lookup)\s+([a-zA-Z0-9_\-\.]+\s*){1,3}$",  # 1-3 terms simple query
]

# Obvious multi-step indicators
_MULTI_STEP_KEYWORDS = [
    " and then ",
    " and also ",
    " after that ",
    " then save ",
    " then write ",
    " then send ",
    " then commit ",
    " then create ",
    " summarize ",
    " and summarize ",
    " and save ",
    " and fix ",
    " research and compare ",
    " search and summarize ",
    " inspect and fix ",
    " prepare for deployment ",
    " prepare project ",
    " build and test ",
    " analyze and report ",
    " step by step ",
    " workflow ",
    " pipeline ",
    " audit and update ",
]


class GoalDetector:
    def __init__(self, api_key_provider=None):
        self._api_key_provider = api_key_provider

    def _get_api_key(self) -> str:
        if callable(self._api_key_provider):
            return self._api_key_provider()
        try:
            config_path = Path(__file__).resolve().parent.parent / "config" / "api_keys.json"
            if config_path.exists():
                return json.loads(config_path.read_text(encoding="utf-8")).get("gemini_api_key", "")
        except Exception:
            pass
        return ""

    def detect(self, user_request: str, context: Optional[dict] = None) -> GoalDetectionResult:
        req = (user_request or "").strip()
        if not req:
            return GoalDetectionResult(is_goal=False)

        req_lower = req.lower()

        # 1. Check for explicit multi-step keywords FIRST
        has_multi_keyword = any(kw in req_lower for kw in _MULTI_STEP_KEYWORDS)
        if has_multi_keyword:
            return GoalDetectionResult(
                is_goal=True,
                goal_title=self._derive_title(req),
                goal_description=req,
                requires_planning=True,
                confidence=0.9,
                estimated_steps=3,
            )

        # 2. Check for action verbs that denote high-level multi-step objectives
        multi_step_verbs = [
            "prepare", "deploy", "refactor", "investigate", "audit", "migrate",
            "diagnose", "research", "compare", "benchmark", "optimize"
        ]
        first_word = req_lower.split()[0] if req_lower.split() else ""
        if first_word in multi_step_verbs or req_lower.count(" and ") >= 2:
            return GoalDetectionResult(
                is_goal=True,
                goal_title=self._derive_title(req),
                goal_description=req,
                requires_planning=True,
                confidence=0.85,
                estimated_steps=4,
            )

        # 3. Quick check for simple atomic commands
        for pat in _SIMPLE_COMMAND_PATTERNS:
            if re.match(pat, req_lower, re.IGNORECASE):
                return GoalDetectionResult(
                    is_goal=False,
                    goal_title=req,
                    confidence=0.95,
                    estimated_steps=1,
                )

        # 4. If request is complex or contains compound clauses (commas + 'and'), consider LLM or heuristic
        word_count = len(req.split())
        if word_count > 12 or ("," in req and " and " in req):
            # Try LLM intent classification if key is available
            api_key = self._get_api_key()
            if api_key:
                try:
                    return self._llm_classify(req, api_key)
                except Exception:
                    pass

            # Fallback heuristic: compound sentence treated as goal
            return GoalDetectionResult(
                is_goal=True,
                goal_title=self._derive_title(req),
                goal_description=req,
                requires_planning=True,
                confidence=0.75,
                estimated_steps=2,
            )

        # Default to single-action command
        return GoalDetectionResult(
            is_goal=False,
            goal_title=req,
            confidence=0.8,
            estimated_steps=1,
        )

    def _derive_title(self, text: str) -> str:
        clean = re.sub(r"^(please|can you|could you|i want you to|iris|hey iris)\s+", "", text, flags=re.IGNORECASE)
        clean = clean.strip()
        if len(clean) > 60:
            clean = clean[:57] + "..."
        return clean.capitalize()

    def _llm_classify(self, text: str, api_key: str) -> GoalDetectionResult:
        from google import genai

        client = genai.Client(api_key=api_key)
        prompt = f"""You are an intent classifier for an AI assistant.
Determine whether this user request is a SIMPLE single-action command or a MULTI-STEP goal requiring sequential tasks/planning.

User Request: "{text}"

Output ONLY a JSON object with this exact structure:
{{
  "is_goal": true,
  "goal_title": "Short title (under 8 words)",
  "goal_description": "Clear goal objective",
  "requires_planning": true,
  "estimated_steps": 3
}}

JSON:"""
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
        )
        raw = (response.text or "").strip()
        raw = re.sub(r"^```[a-zA-Z]*\r?\n?", "", raw)
        raw = re.sub(r"\r?\n?```\s*$", "", raw).strip()
        data = json.loads(raw)
        return GoalDetectionResult(
            is_goal=bool(data.get("is_goal", False)),
            goal_title=data.get("goal_title", self._derive_title(text)),
            goal_description=data.get("goal_description", text),
            requires_planning=bool(data.get("requires_planning", False)),
            confidence=0.9,
            estimated_steps=int(data.get("estimated_steps", 1)),
        )
