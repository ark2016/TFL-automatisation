"""Streamlit UI for regularity checking."""
import sys
import os

# Ensure project root is on sys.path
_project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import streamlit as st
import json

from pumping_lemma.orchestrator.graph import RegularityChecker
from pumping_lemma.parsing.nl_parser import NLParser
from pumping_lemma.llm.client import LLMClient
from pumping_lemma.llm.prompts import SYSTEM_PROMPT, SUGGEST_PROOF_STRATEGY_PROMPT


def get_llm_client(use_llm: bool, model: str) -> LLMClient | None:
    if not use_llm:
        return None
    client = LLMClient(model=model)
    if not client.is_available():
        st.sidebar.warning("⚠️ ANTHROPIC_API_KEY не найден в .env")
        return None
    return client


def render_verdict_badge(verdict: str, confidence: float):
    colors = {
        "regular": ("#28a745", "✅ РЕГУЛЯРНЫЙ"),
        "non_regular": ("#dc3545", "❌ НЕРЕГУЛЯРНЫЙ"),
        "unknown": ("#6c757d", "❓ НЕ ОПРЕДЕЛЕНО"),
    }
    color, label = colors.get(verdict, ("#6c757d", verdict))
    st.markdown(
        f'<div style="background-color:{color};color:white;padding:20px;border-radius:10px;'
        f'text-align:center;font-size:24px;font-weight:bold;margin:10px 0">'
        f'{label}</div>',
        unsafe_allow_html=True,
    )
    st.progress(confidence, text=f"Уверенность: {confidence:.0%}")


def render_proof_trace(proof_trace):
    if not proof_trace:
        return
    with st.expander("📋 Доказательство", expanded=True):
        for step in proof_trace:
            st.markdown(f"- {step}")


def render_heuristic_results(results):
    if not results:
        return
    st.subheader("Результаты эвристик")
    for r in results:
        icon = {"regular": "✅", "non_regular": "❌", "unknown": "❓"}.get(r.verdict, "❓")
        with st.expander(f"{icon} {r.heuristic_name} — {r.verdict} ({r.confidence:.0%})"):
            if r.proof_trace:
                for step in r.proof_trace:
                    st.markdown(f"- {step}")
            if r.counterexample:
                st.json(r.counterexample)


def main():
    st.set_page_config(
        page_title="Проверка регулярности языка",
        page_icon="🔤",
        layout="wide",
    )

    # --- Sidebar ---
    with st.sidebar:
        st.header("⚙️ Настройки")
        pumping_constant = st.slider("Константа накачки", 5, 50, 20)
        mode = st.radio("Режим", ["Полный анализ", "Быстрая проверка"])
        mode_key = "full" if mode == "Полный анализ" else "quick"

        st.divider()
        use_llm = st.checkbox("Использовать LLM (Claude)", value=True)
        model = "claude-sonnet-4-5"
        if use_llm:
            model = st.selectbox("Модель", [
                "claude-sonnet-4-5",
                "claude-haiku-4-5",
                "claude-opus-4-5",
            ])

        st.divider()
        st.subheader("📚 Эвристики")
        st.markdown("""
        1. **Парих** — анализ векторов Париха
        2. **Плотность длин** — зазоры в длинах слов
        3. **Майхилл-Нероуд** — классы эквивалентности
        4. **Замыкание** — пересечение с регулярными
        5. **Нейросимвольная** — LLM + символьная верификация
        """)

    # --- Main ---
    st.title("🔤 Проверка регулярности языка")
    st.markdown("Определяет, является ли формальный язык регулярным, используя 5 эвристик.")

    tab_check, tab_membership, tab_strategy = st.tabs([
        "🔍 Проверка регулярности",
        "🔤 Проверка слова",
        "💡 Стратегия доказательства",
    ])

    # --- Tab 1: Regularity Check ---
    with tab_check:
        description = st.text_area(
            "Описание языка",
            placeholder="Например: {a^n b^n | n >= 0}, палиндромы над {a,b}, a*b*c*",
            height=100,
            key="lang_desc",
        )

        st.markdown("**Быстрые примеры:**")
        cols = st.columns(5)
        examples = [
            ("a^n b^n", "{a^n b^n | n >= 0}"),
            ("ww", "{ww | w ∈ {a,b}*}"),
            ("Палиндромы", "палиндромы над {a,b}"),
            ("a^(n²)", "{a^(n^2) | n >= 1}"),
            ("a*b* (рег.)", "a*b*"),
        ]

        def _set_example(v):
            st.session_state["lang_desc"] = v

        for col, (label, value) in zip(cols, examples):
            col.button(label, use_container_width=True,
                       on_click=_set_example, args=(value,))

        if st.button("🚀 Проверить", type="primary", use_container_width=True):
            if not description.strip():
                st.warning("Введите описание языка")
            else:
                llm = get_llm_client(use_llm, model)
                checker = RegularityChecker(llm_client=llm)
                with st.spinner("Анализ..."):
                    verdict = checker.check(
                        description.strip(),
                        mode=mode_key,
                        pumping_constant=pumping_constant,
                    )
                render_verdict_badge(verdict.verdict, verdict.confidence)
                render_proof_trace(verdict.proof_trace)
                render_heuristic_results(verdict.heuristic_results)

    # --- Tab 2: Word Membership ---
    with tab_membership:
        st.subheader("Проверка принадлежности слова языку")
        lang_desc_mem = st.text_input("Описание языка", key="mem_lang")
        word = st.text_input("Слово для проверки", key="mem_word")

        if st.button("Проверить слово"):
            if lang_desc_mem and word is not None:
                llm = get_llm_client(use_llm, model)
                parser = NLParser(llm)
                spec = parser.parse(lang_desc_mem)
                if spec.has_membership_test():
                    result = spec.accepts(word)
                    if result:
                        st.success(f"✅ Слово '{word}' ПРИНАДЛЕЖИТ языку")
                    else:
                        st.error(f"❌ Слово '{word}' НЕ принадлежит языку")
                else:
                    st.warning("Не удалось построить функцию проверки принадлежности")

    # --- Tab 3: Proof Strategy ---
    with tab_strategy:
        st.subheader("Предложение стратегии доказательства")
        lang_desc_str = st.text_input("Описание языка", key="str_lang")
        user_idea = st.text_input("Ваша идея (опционально)", key="str_idea")

        if st.button("Получить стратегию"):
            llm = get_llm_client(use_llm, model)
            if llm is None:
                st.warning("Для этой функции нужен LLM. Укажите ANTHROPIC_API_KEY в .env")
            elif lang_desc_str:
                with st.spinner("Claude думает..."):
                    previous = f"\nИдея пользователя: {user_idea}" if user_idea else ""
                    prompt = SUGGEST_PROOF_STRATEGY_PROMPT.format(
                        goal="нерегулярности",
                        description=lang_desc_str,
                        previous_attempts=previous,
                    )
                    try:
                        result = llm.complete_json(prompt, system=SYSTEM_PROMPT)
                        st.json(result)
                    except Exception as e:
                        st.error(f"Ошибка: {e}")


if __name__ == "__main__":
    main()
