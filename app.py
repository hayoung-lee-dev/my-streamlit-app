"""AIVLE AI assistant: Streamlit day-2 prototype.
Run: streamlit run app.py
Secrets: OPENAI_API_KEY, optionally GMAIL_ADDRESS and GMAIL_APP_PASSWORD.
"""
import io
import json
import os
import re
import smtplib
from datetime import date, datetime, timedelta
from email.message import EmailMessage

import pandas as pd
import streamlit as st
from openai import OpenAI

st.set_page_config(page_title="에이블 | AI 업무비서", page_icon="🤖", layout="wide")
st.title("🤖 에이블 | AI 업무비서")
st.caption("회의록 분석 · 음성 질의응답 · 업무 우선순위 · 일정 관리")

if not (os.getenv("OPENAI_API_KEY") or st.secrets.get("OPENAI_API_KEY", None)):
    st.error("OPENAI_API_KEY를 Streamlit Secrets 또는 환경 변수에 등록하세요.")
    st.stop()
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY") or st.secrets["OPENAI_API_KEY"])

SYS_ROLE = """당신은 마케팅 데이터 분석가 김 대리의 AI 개인비서 '에이블'입니다.
김 대리는 잦은 외부 미팅과 여러 분석 업무를 병행합니다.
회의록을 근거로 핵심 결정사항과 김 대리의 담당 업무를 빠르게 파악하도록 돕습니다.
추측하지 말고, 원문에 없는 담당자·날짜·중요도는 '확인 필요'라고 말합니다.
사용자가 다른 이름을 지정하면 그 사용자를 위한 비서로 동작합니다.
답변은 명확한 한국어로 간결하게 작성합니다.
"""

for key, default in {
    "messages": [], "document": "", "tasks": [], "events": [],
    "last_answer": "", "last_transcript": "", "last_audio": None, "pending_events": []
}.items():
    if key not in st.session_state:
        st.session_state[key] = default


def extract_txt(uploaded):
    if uploaded is None:
        return ""
    return uploaded.getvalue().decode("utf-8-sig", errors="replace")


def ask_ai(question, document=""):
    system = SYS_ROLE
    if document:
        system += "\n\n[업로드한 회의록]\n" + document[:75000]
    # 최근 10개 사용자/비서 메시지만 모델에 전달
    history = st.session_state.messages[-10:]
    items = [{"role": "system", "content": system}]
    items += history
    items += [{"role": "user", "content": question}]
    result = client.responses.create(model="gpt-4o-mini", input=items)
    answer = result.output_text
    st.session_state.messages = (history + [
        {"role": "user", "content": question},
        {"role": "assistant", "content": answer}
    ])[-10:]
    st.session_state.last_answer = answer
    return answer


def make_speech(text):
    # OpenAI SDK 3.x: 생성된 음성 바이너리를 읽어 st.audio에 전달
    speech = client.audio.speech.create(model="tts-1", voice="alloy", input=text[:4000])
    return speech.content


def json_from_ai(instruction, document):
    result = client.responses.create(
        model="gpt-4o-mini",
        input=[{"role": "system", "content":
                "입력 문서만 근거로 응답하세요. JSON 객체만 출력하며 마크다운은 사용하지 마세요. "
                "확인되지 않은 날짜·중요도·담당자는 임의로 생성하지 마세요."},
               {"role": "user", "content": instruction + "\n\n" + document[:70000]}]
    )
    raw = result.output_text.strip()
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw)
    return json.loads(raw)


def extract_tasks(document):
    instructions = '''회의록에서 명시적으로 지시된 업무만 추출하세요.
반드시 {"tasks": [{"task": "업무명", "owner": "담당자 또는 확인 필요", "due": "YYYY-MM-DD 또는 빈 문자열", "importance": 3, "evidence": "회의록 원문에서 관련된 짧은 구절"}]} 형식으로 출력하세요.
날짜 기준은 회의록에 명확한 연·월·일이 있을 때만 ISO 날짜로 출력하세요.
중요도는 원문에 등급 정보가 없다면 기본 3으로 두세요. 업무마다 임의의 마감일을 만들지 마세요.'''
    return json_from_ai(instructions, document).get("tasks", [])


def priority_score(task):
    # 중요도(1~5) + 긴급도(기한 기준)로 사분면, 사분면 내 세부 점수
    imp = int(task.get("importance", 3))
    due = str(task.get("due", "")).strip()
    days = None
    if due:
        try:
            days = (date.fromisoformat(due) - date.today()).days
        except ValueError:
            pass
    urgency = 5 if days is not None and days <= 1 else (4 if days is not None and days <= 3 else (3 if days is not None and days <= 7 else 1))
    # 미확인 기한은 긴급하다고 단정하지 않음
    quadrant = ("긴급·중요" if imp >= 4 and urgency >= 4 else
                "비긴급·중요" if imp >= 4 else
                "긴급·비중요" if urgency >= 4 else "비긴급·비중요")
    return quadrant, round(imp * 0.6 + urgency * 0.4, 2), days


def ics_events(events):
    def escape_ics(s):
        return str(s).replace("\\", "\\\\").replace("\n", "\\n").replace(",", "\\,").replace(";", "\\;")
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//AIVLE//AIVLE AI Assistant//KO"]
    for i, ev in enumerate(events):
        try:
            day = date.fromisoformat(str(ev["date"]))
        except (ValueError, KeyError):
            continue
        end = day + timedelta(days=1)
        lines += ["BEGIN:VEVENT", f"UID:aivle-{i}-{day:%Y%m%d}@local",
                  f"DTSTAMP:{datetime.utcnow():%Y%m%dT%H%M%SZ}",
                  f"DTSTART;VALUE=DATE:{day:%Y%m%d}",
                  f"DTEND;VALUE=DATE:{end:%Y%m%d}",
                  f"SUMMARY:{escape_ics(ev['title'])}", "END:VEVENT"]
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines).encode("utf-8")


def send_email(to, subject, body):
    address = st.secrets.get("GMAIL_ADDRESS", os.getenv("GMAIL_ADDRESS", ""))
    password = st.secrets.get("GMAIL_APP_PASSWORD", os.getenv("GMAIL_APP_PASSWORD", ""))
    if not (address and password):
        raise RuntimeError("GMAIL_ADDRESS 및 GMAIL_APP_PASSWORD 설정이 필요합니다.")
    msg = EmailMessage()
    msg["From"] = address
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=20) as smtp:
        smtp.login(address, password)
        smtp.send_message(msg)


tab1, tab2, tab3, tab4 = st.tabs(["🎙 AI 비서", "✅ 업무 우선순위", "📅 일정 관리", "✉️ 이메일"])

with tab1:
    st.subheader("회의록 또는 음성 명령 입력")
    uploaded = st.file_uploader("회의록 TXT 파일", type=["txt"], key="meeting_txt")
    if uploaded is not None:
        st.session_state.document = extract_txt(uploaded)
        st.success(f"회의록 읽기 완료: {uploaded.name}")
    audio = st.audio_input("음성으로 질문하기")
    typed = st.text_input("또는 텍스트로 입력", placeholder="에이블, 이번 회의의 핵심 내용과 내 업무를 알려줘")
    col1, col2 = st.columns(2)
    with col1:
        run = st.button("🤖 AI 답변 생성", type="primary", use_container_width=True)
    with col2:
        auto_tts = st.checkbox("답변을 음성으로도 듣기", value=True)

    if run:
        question = typed.strip()
        if audio is not None:
            try:
                transcription = client.audio.transcriptions.create(
                    model="whisper-1", file=("recording.wav", audio.getvalue(), "audio/wav"))
                question = transcription.text
                st.session_state.last_transcript = question
            except Exception as exc:
                st.error(f"음성 인식 실패: {exc}")
        if not question:
            st.warning("음성 녹음 또는 텍스트 질문을 입력해주세요.")
        else:
            st.info(f"인식/입력된 질문: {question}")
            try:
                with st.spinner("답변 준비 중..."):
                    answer = ask_ai(question, st.session_state.document)
                    st.session_state.last_audio = make_speech(answer) if auto_tts else None
            except Exception as exc:
                st.error(f"AI 처리 오류: {exc}")

    if st.session_state.last_answer:
        st.markdown("#### AI 답변")
        st.write(st.session_state.last_answer)
        if st.session_state.last_audio:
            st.audio(st.session_state.last_audio, format="audio/mp3")
    with st.expander("최근 대화 기록 (최대 10개 메시지)"):
        for m in st.session_state.messages:
            st.markdown(f"**{'나' if m['role'] == 'user' else '에이블'}:** {m['content']}")

with tab2:
    st.subheader("업무 추출 및 아이젠하워 매트릭스")
    st.caption("회의록의 업무를 추출하고 중요도는 직접 수정할 수 있습니다. 마감일이 없는 업무는 추정하지 않습니다.")
    if st.button("회의록에서 업무 자동 추출"):
        if not st.session_state.document:
            st.warning("먼저 AI 비서 탭에서 TXT 회의록을 올려주세요.")
        else:
            try:
                with st.spinner("업무 추출 중..."):
                    st.session_state.tasks = extract_tasks(st.session_state.document)
            except Exception as exc:
                st.error(f"업무 추출 실패: {exc}")

    if st.session_state.tasks:
        frame = pd.DataFrame(st.session_state.tasks)
        for col in ["task", "owner", "due", "importance", "evidence"]:
            if col not in frame:
                frame[col] = ""
        frame = frame[["task", "owner", "due", "importance", "evidence"]]
        edited = st.data_editor(frame, hide_index=True, use_container_width=True,
            column_config={
                "task": st.column_config.TextColumn("업무"),
                "owner": st.column_config.TextColumn("담당자"),
                "due": st.column_config.TextColumn("마감일 (YYYY-MM-DD)"),
                "importance": st.column_config.NumberColumn("중요도 (1~5)", min_value=1, max_value=5, step=1),
                "evidence": st.column_config.TextColumn("회의 근거")}, key="task_editor")
        st.session_state.tasks = edited.fillna("").to_dict("records")
        analysed = []
        for task in st.session_state.tasks:
            quadrant, score, days = priority_score(task)
            analysed.append({"업무": task.get("task", ""), "담당자": task.get("owner", ""),
                             "마감일": task.get("due", ""), "중요도": task.get("importance", 3),
                             "사분면": quadrant, "우선순위 점수": score,
                             "D-day": f"D{days:+d}" if days is not None else "미지정"})
        ranked = pd.DataFrame(analysed).sort_values("우선순위 점수", ascending=False)
        st.dataframe(ranked, use_container_width=True, hide_index=True)
        st.download_button("업무 현황 CSV 다운로드", ranked.to_csv(index=False).encode("utf-8-sig"),
                           "task_priorities.csv", mime="text/csv")
        st.markdown("#### 아이젠하워 매트릭스")
        qcols = st.columns(2)
        categories = ["긴급·중요", "비긴급·중요", "긴급·비중요", "비긴급·비중요"]
        for i, category in enumerate(categories):
            with qcols[i % 2]:
                st.markdown(f"**{category}**")
                items = ranked[ranked["사분면"] == category]["업무"].tolist()
                st.write(" / ".join(items) if items else "해당 업무 없음")

with tab3:
    st.subheader("내부 일정 관리")
    st.caption("현재 버전은 Streamlit 내부 임시 일정입니다. Google 캘린더 실계정 등록은 OAuth 연동이 필요합니다.")
    meeting_day = st.date_input("회의 날짜 (상대적 날짜 해석 기준)", value=date.today(), key="meeting_day")
    if st.button("회의록에서 일정 후보 자동 추출"):
        if not st.session_state.document:
            st.warning("먼저 AI 비서 탭에서 회의록을 업로드해주세요.")
        else:
            try:
                instruction = (
                    f"회의 날짜는 {meeting_day.isoformat()}입니다. 회의록에 명시된 일정을 추출하세요. "
                    "반드시 {\"events\": [{\"title\": \"일정명\", \"date\": \"YYYY-MM-DD 또는 빈 문자열\"}]} "
                    "JSON 객체만 출력하세요. 상대적 날짜는 회의 날짜를 기준으로 계산하세요. "
                    "날짜가 불명확하면 빈 문자열로 두세요. 날짜나 시간을 추측하지 마세요."
                )
                st.session_state.pending_events = json_from_ai(instruction, st.session_state.document).get("events", [])
            except Exception as exc:
                st.error(f"일정 추출 실패: {exc}")
    if st.session_state.pending_events:
        st.markdown("#### 등록 전 확인")
        st.caption("날짜와 일정명을 수정한 뒤 승인해주세요. 날짜가 비어 있으면 등록하지 않습니다.")
        candidates = pd.DataFrame(st.session_state.pending_events)
        for col in ["title", "date"]:
            if col not in candidates:
                candidates[col] = ""
        verified = st.data_editor(candidates[["title", "date"]], hide_index=True, use_container_width=True, key="pending_editor")
        if st.button("검토한 일정 등록"):
            added, skipped = 0, 0
            for ev in verified.fillna("").to_dict("records"):
                try:
                    date.fromisoformat(str(ev["date"]))
                    if not str(ev["title"]).strip():
                        raise ValueError("empty title")
                    st.session_state.events.append(ev)
                    added += 1
                except ValueError:
                    skipped += 1
            st.session_state.pending_events = []
            st.success(f"{added}개 등록, {skipped}개 보류")
            st.rerun()
    with st.form("calendar_form", clear_on_submit=True):
        event_name = st.text_input("일정명")
        event_date = st.date_input("날짜", value=date.today())
        event_confirm = st.form_submit_button("확인 후 일정 추가")
    if event_confirm:
        if not event_name.strip():
            st.warning("일정명을 입력해주세요.")
        else:
            st.session_state.events.append({"title": event_name.strip(), "date": event_date.isoformat()})
            st.success("내부 일정에 등록했습니다. (종일 일정)")
    if st.session_state.events:
        event_df = pd.DataFrame(st.session_state.events).sort_values("date")
        st.dataframe(event_df.rename(columns={"title": "일정명", "date": "날짜"}),
                     use_container_width=True, hide_index=True)
        st.download_button("캘린더 파일(.ics) 다운로드", ics_events(st.session_state.events),
                           "aivle_schedule.ics", mime="text/calendar")
        # 같은 날짜는 시간 정보가 없는 종일일정이므로 '확정 충돌'로 판정하지 않음
        duplicates = event_df[event_df.duplicated("date", keep=False)]
        if not duplicates.empty:
            st.info("같은 날짜에 여러 일정이 있습니다. 시간 충돌 여부는 시작·종료 시간 확인이 필요합니다.")

with tab4:
    st.subheader("AI 답변 이메일 발송")
    st.caption("실제 발송은 사용자가 버튼을 눌러 승인한 경우에만 실행됩니다.")
    with st.form("email_form"):
        receiver = st.text_input("받는 사람 이메일")
        subject = st.text_input("제목", value="에이블 AI 비서 업무 요약")
        email_body = st.text_area("메일 본문", value=st.session_state.last_answer, height=220)
        allow_send = st.checkbox("받는 주소와 내용을 확인했습니다")
        send = st.form_submit_button("이메일 발송", disabled=not allow_send)
    if send:
        if not receiver or "@" not in receiver or not email_body.strip():
            st.warning("받는 이메일과 본문을 확인해주세요.")
        else:
            try:
                send_email(receiver, subject, email_body)
                st.success("이메일 발송 완료")
            except Exception as exc:
                st.error(f"발송 실패: {exc}")
