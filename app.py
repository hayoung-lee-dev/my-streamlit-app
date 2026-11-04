"""AIVLE AI assistant: Streamlit day-2 prototype.
Run: streamlit run app.py
Secrets: OPENAI_API_KEY, optionally GMAIL_ADDRESS and GMAIL_APP_PASSWORD.
"""
import io
import uuid
from html import escape
import json
import os
import re
import smtplib
from datetime import date, datetime, timedelta, time
from email.message import EmailMessage

import pandas as pd
import streamlit as st
from openai import OpenAI

st.set_page_config(page_title="에이블 | AI 업무비서", page_icon="🤖", layout="wide")
st.markdown("""<style>
.block-container{max-width:1250px;padding-top:1.6rem;padding-bottom:3rem}
.hero{background:linear-gradient(125deg,#17334f,#245e76);border-radius:20px;padding:25px 29px;color:white;margin-bottom:20px}
.hero h1{font-size:2rem;margin:0;color:white}.hero p{margin:.45rem 0 0;color:#e0f3f9}
.section-note{color:#64748b;font-size:.88rem}
.metric-shell{border:1px solid #e2e8f0;border-radius:14px;padding:17px;background:var(--secondary-background-color)}
.metric-shell .big{font-size:1.65rem;font-weight:800;line-height:1.4}
.metric-shell .small{font-size:.84rem;opacity:.75}
.qbox{border:1px solid #d7e0e9;border-radius:15px;padding:18px;min-height:195px;margin:6px 0;background:var(--secondary-background-color)}
.qhead{font-weight:800;font-size:1.03rem;margin-bottom:5px}
.qhint{font-size:.78rem;opacity:.75;margin-bottom:12px}
.qitem{border:1px solid #dce4ea;border-radius:10px;padding:9px 11px;margin:7px 0;background:var(--background-color);font-size:.87rem;line-height:1.5}
.qitem small{opacity:.72}
.tag{display:inline-block;border-radius:7px;padding:3px 7px;font-size:.75rem;font-weight:700;margin:4px 0}
[data-testid="stDataFrame"]{border-radius:13px;overflow:hidden}
</style>""", unsafe_allow_html=True)
st.markdown('<div class="hero"><h1>🤖 에이블 · AI 업무비서</h1><p>김 대리의 회의 분석부터 업무 실행까지 · 음성 · 우선순위 · 일정 · 이메일</p></div>',unsafe_allow_html=True)

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
    "last_answer": "", "last_transcript": "", "last_audio": None, "pending_events": [], "mail_draft": None, "doc_name": ""
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
    result = client.responses.create(model="gpt-4o-mini", input=items, tools=[EMAIL_TOOL], tool_choice="auto")
    # 어제 실습 미션 7: LLM이 send_email을 선택하면 실제 발송하지 않고 초안 확인 단계로 연결
    calls = [x for x in result.output if x.type == "function_call" and x.name == "send_email"]
    if calls:
        try:
            args = json.loads(calls[0].arguments)
            st.session_state.mail_draft = args
            answer = "이메일 발송 요청을 확인했습니다. '이메일' 탭에서 수신자와 본문을 검토한 후 발송을 승인해주세요."
        except (ValueError, KeyError, TypeError):
            answer = "메일 초안을 읽는 데 실패했습니다. 이메일 탭에서 직접 작성해주세요."
    else:
        answer = result.output_text
    st.session_state.messages = (history + [
        {"role": "user", "content": question},
        {"role": "assistant", "content": answer}
    ])[-10:]
    st.session_state.last_answer = answer
    return answer


def make_speech(text):
    # OpenAI SDK 3.x: 생성된 음성 바이너리를 읽어 st.audio에 전달
    speech = client.audio.speech.create(model="gpt-4o-mini-tts", voice="nova", input=text[:4000])
    return speech.content


# 어제 실습 미션 7: 도구 스키마. 도구 호출 자체는 발송 승인 요청으로 처리.
EMAIL_TOOL = {
    "type": "function", "name": "send_email",
    "description": "사용자가 명시적으로 이메일 발송을 요청했을 때만 메일 초안을 준비합니다. 최종 전송은 사용자가 화면에서 승인합니다.",
    "parameters": {"type": "object", "properties": {
        "to": {"type": "string", "description": "수신 이메일 주소"},
        "subject": {"type": "string", "description": "메일 제목"},
        "body": {"type": "string", "description": "메일 본문, 별표 서식 금지"}},
        "required": ["to", "subject", "body"], "additionalProperties": False},
    "strict": True
}

# 어제 음성 메모 → 할 일 목록 예제의 JSON Schema를 확장
TASK_SCHEMA = {
    "type":"json_schema", "name":"meeting_tasks", "strict":True,
    "schema":{"type":"object","properties":{"tasks":{
        "type":"array","items":{"type":"object","properties":{
            "task":{"type":"string"}, "owner":{"type":"string"},
            "due":{"type":["string","null"]}, "importance":{"type":"integer"},
            "evidence":{"type":"string"}},
            "required":["task","owner","due","importance","evidence"],
            "additionalProperties":False}}},"required":["tasks"],"additionalProperties":False}
}
EVENT_SCHEMA = {
    "type":"json_schema","name":"meeting_events","strict":True,
    "schema":{"type":"object","properties":{"events":{
        "type":"array","items":{"type":"object","properties":{
            "title":{"type":"string"},"date":{"type":["string","null"]},
            "start":{"type":["string","null"]},"end":{"type":["string","null"]}},
            "required":["title","date","start","end"],"additionalProperties":False}}},
            "required":["events"],"additionalProperties":False}
}


def structured_ai(instruction, document, schema):
    result = client.responses.create(
        model="gpt-4o-mini",
        input=[{"role":"system","content":"당신은 김 대리의 AI 비서 에이블입니다. 원문에 없는 일정·업무·날짜·중요도를 지어내지 마세요. 마감일이 없으면 null, 중요도 근거가 없으면 3으로 표시하세요."},
               {"role":"user","content":instruction+"\n\n[회의록]\n"+document[:70000]}],
        text={"format":schema})
    return json.loads(result.output_text)


def extract_tasks(document, meeting_day):
    prompt = f"회의 날짜는 {meeting_day.isoformat()}입니다. 문서에서 명시적으로 할당한 업무만 빠짐없이 추출하세요. due는 YYYY-MM-DD 형식, 불확실하면 null. 상대 날짜는 회의일 기준으로 해석하되 모호하면 null. 중요도는 원문에서 명시적 근거가 없으면 3. evidence는 원문에서 해당 지시를 뒷받침하는 짧은 구절."
    rows = structured_ai(prompt, document, TASK_SCHEMA).get("tasks", [])
    for r in rows:
        r["due"] = r.get("due") or ""
        r["importance"] = max(1,min(5,int(r.get("importance",3))))
    return rows


def extract_events(document, meeting_day):
    prompt = f"회의 날짜 {meeting_day.isoformat()} 기준으로 명시된 회의·일정·마감일만 추출. 날짜는 YYYY-MM-DD, 불명확하면 null. 시간이 명시되어 있다면 start/end는 HH:MM 형식, 미명시되면 null. 종료 시간이 없으면 null. 임의 시간을 설정하지 마세요."
    rows=structured_ai(prompt, document, EVENT_SCHEMA).get("events", [])
    for r in rows:
        for k in ("date","start","end"):
            r[k] = r.get(k) or ""
    return rows


def priority_score(task):
    # 중요도(1~5) + 긴급도(기한 기준)로 사분면, 사분면 내 세부 점수
    try:
        imp = max(1,min(5,int(task.get("importance") or 3)))
    except (ValueError,TypeError):
        imp = 3
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
    def esc(s):
        return str(s).replace("\\", "\\\\").replace("\n", "\\n").replace(",", "\\,").replace(";", "\\;")
    lines=["BEGIN:VCALENDAR","VERSION:2.0","PRODID:-//AIVLE//AI Assistant//KO"]
    for ev in events:
        try:
            day=date.fromisoformat(str(ev.get("date","")))
        except ValueError:
            continue
        s=str(ev.get("start","")).strip(); e=str(ev.get("end","")).strip()
        lines += ["BEGIN:VEVENT", f"UID:{uuid.uuid4()}@aivle.local",f"DTSTAMP:{datetime.utcnow():%Y%m%dT%H%M%SZ}"]
        try:
            start_time=datetime.strptime(s,"%H:%M").time()
            if e:
                end_time=datetime.strptime(e,"%H:%M").time()
                if end_time <= start_time: raise ValueError("end <= start")
                enddt=datetime.combine(day,end_time)
            else:
                enddt=datetime.combine(day,start_time)+timedelta(minutes=30)
            lines += [f"DTSTART;TZID=Asia/Seoul:{datetime.combine(day,start_time):%Y%m%dT%H%M%S}",f"DTEND;TZID=Asia/Seoul:{enddt:%Y%m%dT%H%M%S}"]
        except ValueError:
            lines += [f"DTSTART;VALUE=DATE:{day:%Y%m%d}",f"DTEND;VALUE=DATE:{(day+timedelta(days=1)):%Y%m%d}"]
        lines += [f"SUMMARY:{esc(ev['title'])}","END:VEVENT"]
    return ("\r\n".join(lines+["END:VCALENDAR"])+"\r\n").encode("utf-8")


def conflicts(events):
    """시간이 명시된 일정만 겹침 확정, 종일 일정은 별도 안내."""
    found=[]
    for i,a in enumerate(events):
        if not a.get("start") or not a.get("end") or not a.get("date"): continue
        for b in events[i+1:]:
            if a.get("date")!=b.get("date") or not b.get("start") or not b.get("end"): continue
            try:
                if max(time.fromisoformat(a["start"]),time.fromisoformat(b["start"])) < min(time.fromisoformat(a["end"]),time.fromisoformat(b["end"])):
                    found.append((a["title"],b["title"],a["date"]))
            except ValueError: continue
    return found


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


tab1, tab2, tab3, tab4 = st.tabs(["🎙 AI 비서", "🎯 업무 우선순위", "📅 일정 관리", "✉️ 이메일"])

with st.sidebar:
    st.header("⚙️ 개인 설정")
    user_name = st.text_input("사용자 이름", value="김 대리")
    st.caption("업로드 자료는 현재 세션에서 활용됩니다. 실무 개인정보나 회사 기밀 자료는 업로드하지 마세요.")
    if st.button("대화 기록 초기화",use_container_width=True):
        st.session_state.messages=[]
        st.session_state.last_answer=""
        st.session_state.last_audio=None
        st.rerun()

with tab1:
    st.subheader("음성 명령과 회의록 분석")
    st.caption("TXT 회의록 + 음성 입력 또는 텍스트 질문 · 음성 인식 결과 확인")
    uploaded=st.file_uploader("회의록 TXT 업로드",type=["txt"],key="meeting_txt")
    if uploaded is not None:
        content=extract_txt(uploaded)
        if st.session_state.doc_name != uploaded.name or st.session_state.document != content:
            st.session_state.document=content
            st.session_state.doc_name=uploaded.name
            st.session_state.tasks=[]
            st.session_state.pending_events=[]
        st.success(f"회의록 준비 완료: {uploaded.name} ({len(content):,}자)")
    mic=st.audio_input("음성 명령 녹음")
    audio_file=st.file_uploader("또는 음성 파일(m4a, mp3, wav) 업로드",type=["m4a","mp3","wav"],key="audio_up")
    typed=st.text_input("텍스트로 질문",placeholder="에이블, 회의 내용을 요약하고 김 대리의 업무를 알려줘")
    cc1,cc2=st.columns([2,1])
    with cc1:
        run=st.button("✨ AI 비서 실행",type="primary",use_container_width=True)
    with cc2:
        auto_tts=st.checkbox("음성 답변 재생",value=True)
    if run:
        question=typed.strip()
        audio_to_use=audio_file or mic
        if audio_to_use:
            try:
                raw=audio_to_use.getvalue()
                transcription=client.audio.transcriptions.create(
                    model="whisper-1",
                    file=(audio_to_use.name or "recording.wav",raw))
                question=transcription.text
                st.session_state.last_transcript=question
            except Exception as exc:
                st.error(f"STT 오류: {exc}")
        if not question:
            st.warning("질문을 입력하거나 음성을 녹음하세요.")
        else:
            st.info(f"🎤 인식된 질문: {question}")
            try:
                with st.spinner("에이블이 분석 중입니다..."):
                    # 시스템 역할에 이름 맥락 반영; 회의록에 없는 사실을 만들지 않음
                    answer=ask_ai(f"[현재 사용자: {user_name}]\n{question}",st.session_state.document)
                    st.session_state.last_audio=make_speech(answer) if auto_tts else None
            except Exception as exc:
                st.error(f"AI 호출 오류: {exc}")
    if st.session_state.last_transcript:
        with st.expander("최근 STT 인식 결과"):
            st.write(st.session_state.last_transcript)
    if st.session_state.last_answer:
        st.markdown("#### 에이블의 답변")
        st.write(st.session_state.last_answer)
        if st.session_state.last_audio:
            st.audio(st.session_state.last_audio,format="audio/mp3")
    with st.expander(f"대화 히스토리 · 최근 {len(st.session_state.messages)}개 메시지"):
        for msg in st.session_state.messages:
            st.markdown(f"**{'나' if msg['role']=='user' else '에이블'}:** {msg['content']}")

with tab2:
    st.subheader("업무 우선순위 분석")
    st.caption("회의록에서 할 일을 추출하고 중요도를 직접 조정하세요. 마감일이 없는 업무는 긴급하다고 추정하지 않습니다.")
    meeting_date=st.date_input("회의 기준일",value=date.today(),key="task_meeting_date")
    if st.button("✨ 회의록에서 업무 자동 추출",type="primary"):
        if not st.session_state.document:
            st.warning("먼저 AI 비서 탭에서 회의록을 업로드해주세요.")
        else:
            try:
                with st.spinner("담당자·업무·기한을 추출하고 있습니다..."):
                    st.session_state.tasks=extract_tasks(st.session_state.document,meeting_date)
            except Exception as exc:
                st.error(f"업무 추출 실패: {exc}")
    if st.session_state.tasks:
        tasks=st.session_state.tasks
        missing=sum(not str(x.get("due") or "").strip() for x in tasks)
        scored=[(t,*priority_score(t)) for t in tasks]
        top=sorted(scored,key=lambda x:x[2],reverse=True)
        high=sum(q=="긴급·중요" for _,q,_,_ in scored)
        st.markdown("#### 한눈에 보는 내 업무")
        metric_cols=st.columns(4)
        for col,num,label in zip(metric_cols,[len(tasks),high,missing,len({str(t.get('owner','')) for t in tasks if t.get('owner')})],["추출 업무","긴급·중요","마감일 미확인","담당자 수"]):
            with col:
                st.markdown(f'<div class="metric-shell"><div class="big">{num}</div><div class="small">{label}</div></div>',unsafe_allow_html=True)
        st.markdown("#### 🏅 먼저 확인할 업무 TOP 3")
        for rank,(t,q,score,days) in enumerate(top[:3],start=1):
            with st.container(border=True):
                a,b=st.columns([5,1])
                with a:
                    st.markdown(f"**{rank}. {t.get('task','')}**")
                    st.caption(f"담당: {t.get('owner','확인 필요')}  ·  기한: {t.get('due') or '확인 필요'}  ·  분류: {q}")
                with b:
                    st.metric("우선점수",f"{score:.1f} / 5")
        st.markdown("#### 🧭 아이젠하워 매트릭스")
        st.caption("가로: 중요도 · 세로: 긴급도 / 각 사분면의 업무를 카드로 표시합니다.")
        qs=[("긴급·중요","1사분면 · 즉시 실행","#d64545"),("비긴급·중요","2사분면 · 계획 수립","#2478ba"),("긴급·비중요","3사분면 · 위임 검토","#c27b17"),("비긴급·비중요","4사분면 · 보류 검토","#718096")]
        for offset in [0,2]:
            cols=st.columns(2,gap="medium")
            for col,(q,hint,accent) in zip(cols,qs[offset:offset+2]):
                with col:
                    subset=[(t,s,d) for t,c,s,d in scored if c==q]
                    items="".join(
                        f'<div class="qitem"><strong>{escape(str(t.get("task","")))}</strong><br><small>👤 {escape(str(t.get("owner","확인 필요")))} · 📅 {escape(str(t.get("due") or "미지정"))} · 점수 {s:.1f}</small></div>'
                        for t,s,d in sorted(subset,key=lambda x:x[1],reverse=True))
                    if not items: items='<div class="qitem">해당 업무 없음</div>'
                    st.markdown(f'<div class="qbox" style="border-top:5px solid {accent}"><div class="qhead">{escape(q)} <span style="color:{accent}">({len(subset)})</span></div><div class="qhint">{escape(hint)}</div>{items}</div>',unsafe_allow_html=True)
        st.markdown("#### ✍️ 업무 정보 수정")
        st.caption("중요도 1~5와 마감일을 수정하면 우선순위 결과에 반영됩니다. 날짜는 YYYY-MM-DD 형식으로 입력하세요.")
        f=pd.DataFrame(tasks)
        for c in ["task","owner","due","importance","evidence"]:
            if c not in f:f[c]=""
        edit=st.data_editor(f[["task","owner","due","importance","evidence"]],hide_index=True,use_container_width=True,num_rows="dynamic",key="task_editor",
           column_config={"task":st.column_config.TextColumn("업무",width="large"),"owner":st.column_config.TextColumn("담당자"),"due":st.column_config.TextColumn("마감일 YYYY-MM-DD"),"importance":st.column_config.NumberColumn("중요도 1~5",min_value=1,max_value=5,step=1),"evidence":st.column_config.TextColumn("원문 근거",width="large")})
        if st.button("💾 수정사항 반영",use_container_width=True):
            clean=[]
            invalid=[]
            for i,t in enumerate(edit.fillna("").to_dict("records"),1):
                if not str(t.get("task","")).strip():continue
                due=str(t.get("due","")).strip()
                if due:
                    try:date.fromisoformat(due)
                    except ValueError:invalid.append(i)
                try:t["importance"]=max(1,min(5,int(t.get("importance") or 3)))
                except (ValueError,TypeError):t["importance"]=3
                clean.append(t)
            if invalid:st.error(f"마감일 형식을 확인하세요. 행 번호: {invalid}")
            else:
                st.session_state.tasks=clean
                st.success("수정사항을 반영했습니다.")
                st.rerun()
        table=[]
        for t in tasks:
            q,score,days=priority_score(t)
            table.append({"업무":t.get("task",""),"담당자":t.get("owner",""),"마감일":t.get("due",""),"중요도":t.get("importance",3),"분류":q,"우선점수":score,"D-day":("D"+str(days) if days is not None else "미지정")})
        df=pd.DataFrame(table).sort_values("우선점수",ascending=False)
        with st.expander("📋 우선순위 전체 순위표",expanded=False):
            st.dataframe(df,hide_index=True,use_container_width=True)
        st.download_button("⬇️ 업무 현황 CSV 다운로드",df.to_csv(index=False).encode("utf-8-sig"),"aivle_tasks.csv",mime="text/csv")
        st.caption("점수 = 중요도 × 0.6 + 마감 긴급도 × 0.4 (예시 가중치). 마감일 미지정은 긴급도 1로 임시 계산하므로 우선순위 해석 시 주의하세요.")
    else:
        st.info("회의록 업로드 후 '업무 자동 추출'을 실행하면 업무 카드와 4분면이 나타납니다.")

with tab3:
    st.subheader("캘린더 · 일정 충돌 탐지")
    st.caption("Google 캘린더 연동 전 내부 프로토타입입니다. 승인한 일정만 등록하고 .ics 파일로 내보낼 수 있습니다.")
    meeting_day=st.date_input("회의 날짜 (상대 날짜 해석 기준)",value=date.today(),key="meeting_day")
    if st.button("✨ 회의록에서 일정 후보 추출",type="primary"):
        if not st.session_state.document:st.warning("먼저 회의록을 업로드해주세요.")
        else:
            try:
                with st.spinner("일정 후보를 추출 중입니다..."):
                    st.session_state.pending_events=extract_events(st.session_state.document,meeting_day)
            except Exception as exc:st.error(f"일정 추출 오류: {exc}")
    if st.session_state.pending_events:
        st.markdown("#### 등록 전 승인")
        st.caption("날짜 및 시간을 직접 수정할 수 있습니다. 시간이 없으면 종일 일정으로 등록됩니다.")
        candidates=pd.DataFrame(st.session_state.pending_events)
        for c in ["title","date","start","end"]:
            if c not in candidates:candidates[c]=""
        verified=st.data_editor(candidates[["title","date","start","end"]],hide_index=True,use_container_width=True,num_rows="dynamic",key="pending_editor",
            column_config={"title":"일정명","date":"날짜 YYYY-MM-DD","start":"시작 HH:MM","end":"종료 HH:MM"})
        if st.button("✅ 선택한 일정 등록 승인"):
            added=0; skipped=0
            for ev in verified.fillna("").to_dict("records"):
                try:
                    date.fromisoformat(str(ev["date"]))
                    if not str(ev["title"]).strip():raise ValueError("no title")
                    if ev["start"]:time.fromisoformat(str(ev["start"]))
                    if ev["end"]:time.fromisoformat(str(ev["end"]))
                    if bool(ev["end"]) and not ev["start"]:raise ValueError("end without start")
                    if ev["start"] and ev["end"] and str(ev["end"])<=str(ev["start"]):raise ValueError("end <= start")
                    st.session_state.events.append(ev);added+=1
                except ValueError:skipped+=1
            st.session_state.pending_events=[]
            st.success(f"{added}개 등록, {skipped}개 보류")
            st.rerun()
    with st.expander("일정 직접 등록"):
        with st.form("calendar_form",clear_on_submit=True):
            event_name=st.text_input("일정명")
            event_date=st.date_input("날짜",value=date.today())
            cs,ce=st.columns(2)
            with cs:time_start=st.text_input("시작 시간 (선택, HH:MM)")
            with ce:time_end=st.text_input("종료 시간 (선택, HH:MM)")
            event_submit=st.form_submit_button("일정 추가")
        if event_submit:
            try:
                if not event_name.strip():raise ValueError("일정명이 비어 있습니다.")
                if time_start:time.fromisoformat(time_start)
                if time_end:time.fromisoformat(time_end)
                if time_end and (not time_start or time_end<=time_start):raise ValueError("종료 시간은 시작 시간보다 늦어야 합니다.")
                st.session_state.events.append({"title":event_name.strip(),"date":event_date.isoformat(),"start":time_start,"end":time_end})
                st.success("일정이 추가됐습니다.")
            except ValueError as e:st.error(f"시간 또는 일정명 확인: {e}")
    if st.session_state.events:
        evdf=pd.DataFrame(st.session_state.events)
        st.markdown("#### 현재 등록된 일정")
        st.dataframe(evdf.rename(columns={"title":"일정명","date":"날짜","start":"시작","end":"종료"}).sort_values("날짜"),hide_index=True,use_container_width=True)
        clashes=conflicts(st.session_state.events)
        if clashes:
            for a,b,day in clashes:st.error(f"⚠️ 시간 충돌: {day} — {a} / {b}")
        elif any(not e.get("start") or not e.get("end") for e in st.session_state.events):
            st.info("시간 정보가 없는 일정은 정확한 충돌 판정에서 제외했습니다.")
        else:st.success("확인된 시간 충돌 없음")
        st.download_button("📥 캘린더 일정 (.ics)",ics_events(st.session_state.events),"aivle_schedule.ics",mime="text/calendar")

with tab4:
    st.subheader("✉️ 이메일 발송")
    st.caption("미션 7의 LLM Function Calling 기반 초안 작성 + 사용자 승인 후 실제 발송")
    if st.session_state.mail_draft:st.info("AI가 메일 초안을 준비했습니다. 내용을 확인해주세요.")
    draft=st.session_state.mail_draft or {}
    with st.form("email_form"):
        receiver=st.text_input("받는 사람 이메일",value=draft.get("to",""))
        subject=st.text_input("제목",value=draft.get("subject","에이블 AI 비서 업무 요약"))
        email_body=st.text_area("본문",value=draft.get("body",st.session_state.last_answer),height=220)
        allow_send=st.checkbox("받는 주소와 메일 본문을 확인했습니다")
        send=st.form_submit_button("📨 최종 승인 및 발송",disabled=not allow_send)
    if send:
        if not receiver.strip() or "@" not in receiver or not email_body.strip():st.warning("이메일 주소와 본문을 확인해주세요.")
        else:
            try:
                send_email(receiver.strip(),subject,email_body)
                st.session_state.mail_draft=None
                st.success("이메일 발송 완료")
            except Exception as exc:st.error(f"발송 실패: {exc}")
