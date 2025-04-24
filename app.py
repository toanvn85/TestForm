import streamlit as st
st.set_page_config(page_title="Bài kiểm tra đào tạo ISO 50001:2018", layout="wide")

import pandas as pd, gspread, hashlib, time, os, re
from datetime import datetime
from google.oauth2.service_account import Credentials
import plotly.express as px
from PIL import Image
# Import libraries for export functionality
import io
import base64
from reportlab.lib.pagesizes import letter, A4
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch, cm
import xlsxwriter

# ------------ Cấu hình logo 2×3 cm ~ 76×113 px ------------
LOGO_WIDTH, LOGO_HEIGHT = int(3/2.54*96), int(3/2.54*96)
def display_logos():
    """Tự động tìm và hiển thị logo1.* và logo2.* với đa định dạng."""
    c1, c2, c3, c4, c5 = st.columns(5)
    for col, base in ((c1, "logo1"), (c3, "logo2"), (c5, "logo3")):
        found = None
        for ext in ("png","jpg","jpeg","gif"):
            path = f"{base}.{ext}"
            if os.path.exists(path):
                found = path
                break
        if found:
            try:
                img = Image.open(found).resize((LOGO_WIDTH, LOGO_HEIGHT))
                col.image(img)
            except Exception as e:
                col.error(f"Lỗi đọc {found}: {e}")
        else:
            col.warning(f"Thiếu {base}.(png/jpg/jpeg/gif)")

# ------------ Thiết lập Google Sheets ------------
SCOPE = ["https://www.googleapis.com/auth/spreadsheets",
         "https://www.googleapis.com/auth/drive"]

def retry(func, tries=5, delay=1, mult=2):
    for i in range(tries):
        try:
            return func()
        except gspread.exceptions.APIError as e:
            if "429" in str(e) and i < tries-1:
                st.warning(f"Giới hạn tốc độ, thử lại sau {delay}s…")
                time.sleep(delay)
                delay *= mult
            else:
                raise

@st.cache_resource
def gclient():
    if os.path.exists("credentials.json"):
        creds = Credentials.from_service_account_file("credentials.json", scopes=SCOPE)
    else:
        creds = Credentials.from_service_account_info(
            st.secrets["gcp_service_account"], scopes=SCOPE
        )
    return gspread.authorize(creds)

def ensure_header(ws, header):
    cur = [c.lower() for c in ws.row_values(1)]
    tgt = [h.lower() for h in header]
    if cur != tgt:
        ws.resize(rows=max(ws.row_count,1), cols=len(header))
        ws.update(f"A1:{chr(64+len(header))}1", [header])

@st.cache_resource(ttl=3600)
def gws():
    cli = gclient()
    # Users_DB
    try: udb = cli.open("Users_DB")
    except gspread.exceptions.SpreadsheetNotFound:
        udb = cli.create("Users_DB")
        udb.add_worksheet("Admin", rows=10, cols=2)
        udb.add_worksheet("Users", rows=100, cols=10)
    users_ws = udb.worksheet("Users")
    admin_ws = udb.worksheet("Admin")
    ensure_header(users_ws, [
        "company","full_name","email","position","department",
        "gender","password","confirm_password"
    ])
    ensure_header(admin_ws, ["username","password"])
    if len(admin_ws.get_all_values()) == 1:
        admin_ws.append_row([
            "admin",
            hashlib.sha256("admin123".encode()).hexdigest()
        ])
    # Default user nếu cần
    if len(users_ws.get_all_values()) == 1:
        pw0 = hashlib.sha256("user123".encode()).hexdigest()
        users_ws.append_row([
            "Công ty mặc định","Người dùng","user@example.com",
            "Học sinh","CNTT","Nam",pw0,pw0
        ])
    # Quiz_Questions
    try:
        quiz_wb = cli.open("Quiz_Questions")
        ques_ws = quiz_wb.worksheet("Questions")
    except gspread.exceptions.SpreadsheetNotFound:
        quiz_wb = cli.create("Quiz_Questions")
        ques_ws = quiz_wb.sheet1
        ques_ws.update_title("Questions")
        ensure_header(ques_ws, [
            "question id","question text","options","correct answers","points"
        ])
    except gspread.exceptions.WorksheetNotFound:
        quiz_wb = cli.open("Quiz_Questions")
        ques_ws = quiz_wb.add_worksheet("Questions", rows=1, cols=5)
        ensure_header(ques_ws, [
            "question id","question text","options","correct answers","points"
        ])
    # Quiz_Responses
    try: rsp_wb = cli.open("Quiz_Responses")
    except gspread.exceptions.SpreadsheetNotFound:
        rsp_wb = cli.create("Quiz_Responses")
        rsp_wb.add_worksheet("Responses", rows=1, cols=10)
    try: rsp_ws = rsp_wb.worksheet("Responses")
    except gspread.exceptions.WorksheetNotFound:
        rsp_ws = rsp_wb.sheet1
        rsp_ws.update_title("Responses")
    ensure_header(rsp_ws, [
        "email","question id","selected answers","is correct",
        "score","timestamp","edit no"
    ])
    return {
        "users": users_ws,
        "admin": admin_ws,
        "ques": ques_ws,
        "rsp_wb": rsp_wb,
        "rsp_ws": rsp_ws
    }

# ------------ DataFrame Helpers ------------
def _df(ws):
    data = ws.get_all_values()
    if len(data) <= 1:
        cols = [c.lower() for c in data[0]] if data else []
        return pd.DataFrame(columns=cols)
    return pd.DataFrame(data[1:], columns=[c.lower() for c in data[0]])

@st.cache_data(ttl=300)
def df_users():      return _df(gws()["users"])
@st.cache_data(ttl=300)
def df_questions():  return _df(gws()["ques"])
@st.cache_data(ttl=300)
def df_responses():  return _df(gws()["rsp_ws"])

# ------------ Utilities ------------
hash_pw    = lambda x: hashlib.sha256(x.encode()).hexdigest()
verify_pw  = lambda s,p: s.strip()==hash_pw(p.strip())
cmp_ans    = lambda sel,cor: {s.strip().upper() for s in sel}=={c.strip().upper() for c in cor}
sheet_name = lambda em: re.sub(r'[^A-Za-z0-9_-]','_',em)[:100]

def reset_admin_pw():
    hashed = hash_pw("admin123")
    gws()["admin"].update("B2", [[hashed]])
    st.success("Đã thiết lập lại mật khẩu Admin về **default**")

# ------------ Export Functions ------------
def generate_excel(df, sheet_name="Data"):
    """Generate Excel file from DataFrame with proper styling"""
    output = io.BytesIO()
    
    # Create a workbook and add a worksheet
    workbook = xlsxwriter.Workbook(output, {'in_memory': True})
    worksheet = workbook.add_worksheet(sheet_name)
    
    # Add formats
    header_format = workbook.add_format({
        'bold': True,
        'bg_color': '#D9EAD3',
        'border': 1,
        'text_wrap': True,
        'valign': 'vcenter',
        'align': 'center'
    })
    
    cell_format = workbook.add_format({
        'border': 1,
        'text_wrap': True,
        'valign': 'vcenter',
        'align': 'left'
    })
    
    correct_format = workbook.add_format({
        'border': 1,
        'text_wrap': True,
        'valign': 'vcenter',
        'align': 'left',
        'bg_color': '#E2EFDA'
    })
    
    incorrect_format = workbook.add_format({
        'border': 1,
        'text_wrap': True,
        'valign': 'vcenter',
        'align': 'left',
        'bg_color': '#FCE4D6'
    })

    # Write the column headers
    for col_num, column in enumerate(df.columns):
        worksheet.write(0, col_num, column, header_format)
        worksheet.set_column(col_num, col_num, 15)
    
    # Write the data with conditional formatting
    for row_num, row in enumerate(df.values):
        for col_num, cell_value in enumerate(row):
            if 'ok' in df.columns and col_num == list(df.columns).index('ok'):
                if str(cell_value).lower() == 'true':
                    worksheet.write(row_num + 1, col_num, cell_value, correct_format)
                else:
                    worksheet.write(row_num + 1, col_num, cell_value, incorrect_format)
            else:
                worksheet.write(row_num + 1, col_num, cell_value, cell_format)
    
    # Auto-fit columns
    for col_num, column in enumerate(df.columns):
        max_len = max([len(str(value)) for value in df[column].values] + [len(column)]) + 2
        worksheet.set_column(col_num, col_num, min(max_len, 30))
    
    workbook.close()
    output.seek(0)
    
    return output

def generate_pdf(df, title="Data Export", email=None):
    """Generate PDF report from DataFrame with proper formatting"""
    buffer = io.BytesIO()
    
    # Create the PDF document
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=72,
        leftMargin=72,
        topMargin=72,
        bottomMargin=72
    )
    
    # Container for elements to be added to the PDF
    elements = []
    
    # Define styles
    styles = getSampleStyleSheet()
    title_style = styles['Title']
    heading_style = styles['Heading2']
    normal_style = styles['Normal']
    
    # Add title
    elements.append(Paragraph(title, title_style))
    elements.append(Spacer(1, 12))
    
    if email:
        elements.append(Paragraph(f"Email: {email}", heading_style))
        elements.append(Spacer(1, 12))
    
    # Add timestamp
    current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    elements.append(Paragraph(f"Generated on: {current_time}", normal_style))
    elements.append(Spacer(1, 24))
    
    # Add summary statistics if available
    if not df.empty:
        if all(col in df.columns for col in ['ok', 'score']):
            total_questions = len(df)
            correct_answers = (df['ok'] == 'True').sum()
            total_score = sum(pd.to_numeric(df['score'], errors='coerce').fillna(0))
            
            elements.append(Paragraph("Summary Statistics:", heading_style))
            elements.append(Paragraph(f"Total Questions Answered: {total_questions}", normal_style))
            elements.append(Paragraph(f"Correct Answers: {correct_answers}", normal_style))
            elements.append(Paragraph(f"Total Score: {total_score}", normal_style))
            accuracy = (correct_answers/total_questions*100) if total_questions > 0 else 0
            elements.append(Paragraph(f"Accuracy: {accuracy:.1f}%", normal_style))
            elements.append(Spacer(1, 24))
    
    # Create table data
    data = [df.columns.tolist()]
    for _, row in df.iterrows():
        data.append([str(cell) for cell in row])
    
    # Create the table
    if data:
        table = Table(data, repeatRows=1)
        
        # Add table style
        table_style = TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.lightgreen),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.black),
            ('ALIGN', (0, 0), (-1, 0), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 12),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
            ('BACKGROUND', (0, 1), (-1, -1), colors.white),
            ('TEXTCOLOR', (0, 1), (-1, -1), colors.black),
            ('ALIGN', (0, 1), (-1, -1), 'LEFT'),
            ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
            ('FONTSIZE', (0, 1), (-1, -1), 10),
            ('GRID', (0, 0), (-1, -1), 1, colors.black),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ])
        
        # Add conditional formatting for 'ok' column if it exists
        if 'ok' in df.columns:
            ok_col_idx = df.columns.get_loc('ok')
            for row_idx, row in enumerate(data[1:], 1):
                if row[ok_col_idx].lower() == 'true':
                    table_style.add('BACKGROUND', (0, row_idx), (-1, row_idx), colors.lightgreen)
                else:
                    table_style.add('BACKGROUND', (0, row_idx), (-1, row_idx), colors.lightsalmon)
        
        table.setStyle(table_style)
        elements.append(table)
    
    # Build the PDF document
    doc.build(elements)
    buffer.seek(0)
    return buffer

def get_download_link(buffer, filename, text):
    """Generate a download link for a file"""
    b64 = base64.b64encode(buffer.read()).decode()
    href = f'<a href="data:application/octet-stream;base64,{b64}" download="{filename}">{text}</a>'
    return href

# ------------ Export Helper Functions ------------
def add_export_buttons(my_df, email=None):
    """Add export buttons to the sidebar"""
    st.sidebar.markdown("### Export Options")
    
    if my_df.empty:
        st.sidebar.info("No data available to export")
        return
    
    col1, col2 = st.sidebar.columns(2)
    
    # Excel export
    if col1.button("📊 Export to Excel"):
        with st.spinner("Generating Excel file..."):
            try:
                excel_buffer = generate_excel(my_df, sheet_name=f"Results_{email}")
                st.sidebar.markdown(
                    get_download_link(excel_buffer, f"results_{email.split('@')[0]}.xlsx", "📥 Download Excel"),
                    unsafe_allow_html=True
                )
                st.sidebar.success("Excel file generated successfully!")
            except Exception as e:
                st.sidebar.error(f"Error generating Excel file: {e}")
    
    # PDF export
    if col2.button("📄 Export to PDF"):
        with st.spinner("Generating PDF file..."):
            try:
                pdf_buffer = generate_pdf(my_df, title="Quiz Results Report", email=email)
                st.sidebar.markdown(
                    get_download_link(pdf_buffer, f"results_{email.split('@')[0]}.pdf", "📥 Download PDF"),
                    unsafe_allow_html=True
                )
                st.sidebar.success("PDF file generated successfully!")
            except Exception as e:
                st.sidebar.error(f"Error generating PDF file: {e}")

def add_admin_export_buttons(stats_df):
    """Add export buttons for admin statistics"""
    st.sidebar.markdown("### Export Options")
    
    if stats_df.empty:
        st.sidebar.info("No data available to export")
        return
    
    col1, col2 = st.sidebar.columns(2)
    
    # Excel export
    if col1.button("📊 Export Statistics to Excel"):
        with st.spinner("Generating Excel file..."):
            try:
                excel_buffer = generate_excel(stats_df, sheet_name="Participant_Statistics")
                st.sidebar.markdown(
                    get_download_link(excel_buffer, "participant_statistics.xlsx", "📥 Download Excel"),
                    unsafe_allow_html=True
                )
                st.sidebar.success("Excel file generated successfully!")
            except Exception as e:
                st.sidebar.error(f"Error generating Excel file: {e}")
    
    # PDF export
    if col2.button("📄 Export Statistics to PDF"):
        with st.spinner("Generating PDF file..."):
            try:
                pdf_buffer = generate_pdf(stats_df, title="Participant Statistics Report")
                st.sidebar.markdown(
                    get_download_link(pdf_buffer, "participant_statistics.pdf", "📥 Download PDF"),
                    unsafe_allow_html=True
                )
                st.sidebar.success("PDF file generated successfully!")
            except Exception as e:
                st.sidebar.error(f"Error generating PDF file: {e}")

def admin_export_participant_results():
    """Allow admin to export results for a specific participant"""
    st.sidebar.markdown("### Export Participant Results")
    
    # Get all participant emails
    rd = df_responses()
    if rd.empty or "email" not in rd.columns:
        st.sidebar.info("No participant data available")
        return
    
    # Get unique emails
    emails = rd["email"].unique().tolist()
    
    # Email selection dropdown
    selected_email = st.sidebar.selectbox(
        "Select participant",
        options=emails,
        format_func=lambda x: x
    )
    
    if selected_email:
        # Get participant's responses
        participant_data = rd[rd["email"] == selected_email]
        
        col1, col2 = st.sidebar.columns(2)
        
        # Excel export
        if col1.button("📊 Export to Excel", key="admin_excel"):
            with st.spinner("Generating Excel file..."):
                try:
                    excel_buffer = generate_excel(participant_data, sheet_name=f"Results_{selected_email}")
                    st.sidebar.markdown(
                        get_download_link(excel_buffer, f"results_{selected_email.split('@')[0]}.xlsx", "📥 Download Excel"),
                        unsafe_allow_html=True
                    )
                    st.sidebar.success("Excel file generated successfully!")
                except Exception as e:
                    st.sidebar.error(f"Error generating Excel file: {e}")
        
        # PDF export
        if col2.button("📄 Export to PDF", key="admin_pdf"):
            with st.spinner("Generating PDF file..."):
                try:
                    pdf_buffer = generate_pdf(participant_data, title="Participant Results Report", email=selected_email)
                    st.sidebar.markdown(
                        get_download_link(pdf_buffer, f"results_{selected_email.split('@')[0]}.pdf", "📥 Download PDF"),
                        unsafe_allow_html=True
                    )
                    st.sidebar.success("PDF file generated successfully!")
                except Exception as e:
                    st.sidebar.error(f"Error generating PDF file: {e}")

# ============ Trang Đăng nhập / Đăng ký ============
def page_login():
    display_logos()
    st.title("Đăng nhập")
    tab_login, tab_reg = st.tabs(["Đăng nhập","Đăng ký"])
    # Đăng nhập
    with tab_login:
        vai_tro = st.radio("Vai trò", ["Quản trị","Học viên"])
        if vai_tro=="Quản trị":
            pw = st.text_input("Mật khẩu Admin", type="password")
            c1,c2 = st.columns(2)
            if c1.button("Đăng nhập"):
                stored = gws()["admin"].cell(2,2).value
                if verify_pw(stored,pw):
                    st.session_state.role="admin"; st.rerun()
                else: st.error("Mật khẩu không đúng")
            if c2.button("Đặt lại mật khẩu"):
                reset_admin_pw()
        else:
            em = st.text_input("Email")
            pw = st.text_input("Mật khẩu", type="password")
            if st.button("Đăng nhập"):
                u = df_users(); r = u[u['email']==em]
                if r.empty:
                    st.error("Không tìm thấy người dùng")
                elif verify_pw(r.iloc[0]['password'], pw):
                    st.session_state.role="part"
                    st.session_state.email=em
                    st.rerun()
                else:
                    st.error("Mật khẩu không đúng")
    # Đăng ký
    with tab_reg:
        with st.form("reg"):
            cp = st.text_input("Công ty")
            nm = st.text_input("Họ và tên")
            em = st.text_input("Email")
            ps = st.text_input("Chức vụ")
            dt = st.text_input("Phòng ban")
            gd = st.selectbox("Giới tính",["Nam","Nữ","Khác"])
            p1 = st.text_input("Mật khẩu", type="password")
            p2 = st.text_input("Xác nhận mật khẩu", type="password")
            ok = st.form_submit_button("Đăng ký")
        if ok:
            if p1!=p2:
                st.error("Mật khẩu không khớp.")
            elif df_users()['email'].eq(em).any():
                st.error("Email đã tồn tại.")
            else:
                hp = hash_pw(p1)
                retry(lambda: gws()["users"].append_row(
                    [cp,nm,em,ps,dt,gd,hp,hp]
                ))
                df_users.clear()
                st.success("Đăng ký thành công!")

# ============ Trang Quản trị ============
def page_admin():
    display_logos()
    st.title("Bảng điều khiển Quản trị")
    tab_m, tab_s, tab_pw = st.tabs([
        "Quản lý câu hỏi","Thống kê","Đổi mật khẩu"
    ])

    # Quản lý câu hỏi
    with tab_m:
        qd   = df_questions(); ws_q = gws()["ques"]
        eid  = st.session_state.get("edit_id")
        md   = st.session_state.get("add_mode")
        st.subheader(f"Tổng số câu hỏi: {len(qd)}")
        # Đánh lại ID nếu cần
        if not qd.empty:
            cur = sorted(qd["question id"].astype(int))
            exp = list(range(1,len(qd)+1))
            if cur!=exp:
                st.warning("ID không liên tục, đang đánh số lại...")
                mapping={o:n for o,n in zip(cur,exp)}
                for old,new in mapping.items():
                    ridx=qd[qd["question id"].astype(int)==old].index[0]+2
                    ws_q.update_cell(ridx,1,str(new))
                df_questions.clear(); st.success("Xong"); st.rerun()
        # Show & Edit
        if not qd.empty:
            qd["question id"]=qd["question id"].astype(int)
            qd=qd.sort_values("question id")
            for _,r in qd.iterrows():
                qid=int(r["question id"])
                if eid==qid:
                    st.markdown(f"### ✏️ Chỉnh sửa câu hỏi {qid}")
                    with st.form(f"edit_{qid}"):
                        txt=st.text_area("Nội dung câu hỏi", value=r["question text"])
                        opts=st.text_area("Các phương án", value=r["options"])
                        labs=[l.split('.')[0].strip() for l in opts.splitlines() if l.strip()]
                        st.write("Đáp án đúng:")
                        prev=set(r["correct answers"].split(','))
                        corr=[]; cols=st.columns(len(labs))
                        for i,lab in enumerate(labs):
                            if cols[i].checkbox(lab, value=lab in prev, key=f"cb_{qid}_{lab}"):
                                corr.append(lab)
                        pts=st.number_input("Điểm",min_value=1,value=int(r["points"]))
                        luu=st.form_submit_button("Lưu"); huy=st.form_submit_button("Hủy")
                    if luu:
                        rownew=[str(qid),txt,opts,",".join(corr),str(int(pts))]
                        ridx=qd[qd["question id"]==qid].index[0]+2
                        retry(lambda: ws_q.update(f"A{ridx}:E{ridx}",[rownew]))
                        df_questions.clear(); st.session_state.pop("edit_id")
                        st.success("Đã lưu"); st.rerun()
                    if huy:
                        st.session_state.pop("edit_id"); st.rerun()
                else:
                    st.markdown(f"### {qid}. {r['question text']}")
                    corr=set(r["correct answers"].split(','))
                    for line in r["options"].splitlines():
                        lab=line.split('.')[0].strip()
                        st.checkbox(line, value=lab in corr, disabled=True,
                                    key=f"ro_{qid}_{lab}")
                    st.caption(f"Điểm: {r['points']}")
                    if st.button("Chỉnh sửa", key=f"btn_{qid}"):
                        st.session_state.edit_id=qid; st.rerun()
                    st.write("---")
        # Thêm mới
        if md:
            nid = int(qd["question id"].astype(int).max())+1 if not qd.empty else 1
            st.markdown(f"### ➕ Thêm câu hỏi {nid}")
            with st.form("add"):
                txt=st.text_area("Nội dung câu hỏi")
                opts=st.text_area("Các phương án",value="A. \nB. \nC. \nD. ")
                labs=[l.split('.')[0].strip() for l in opts.splitlines() if l.strip()]
                st.write("Đáp án đúng:")
                corr=[]; cols=st.columns(len(labs))
                for i,lab in enumerate(labs):
                    if cols[i].checkbox(lab, key=f"new_{lab}"): corr.append(lab)
                pts=st.number_input("Điểm",min_value=1,value=1)
                luu=st.form_submit_button("Lưu"); huy=st.form_submit_button("Hủy")
            if luu:
                retry(lambda: ws_q.append_row(
                    [str(nid),txt,opts,",".join(corr),str(int(pts))]
                ))
                df_questions.clear(); st.session_state.pop("add_mode")
                st.success("Đã thêm"); st.rerun()
            if huy:
                st.session_state.pop("add_mode"); st.rerun()
        if not eid and not md:
            if st.button("➕ Thêm mới"):
                st.session_state["add_mode"]=True; st.rerun()

    # Thống kê
   # Thống kê (continued)
    with tab_s:
        qd = df_questions(); rd = df_responses()
        if rd.empty:
            st.info("Chưa có phản hồi nào.")
        else:
            if "email" not in rd.columns:
                for alt in ("User Email","user email","Email", "email"):
                    if alt in rd.columns:
                        rd = rd.rename(columns={alt:"email"}); break
            tot=len(qd)
            stt = (
                rd.groupby("email")
                  .agg(
                    Đã_trả_lời=("question id","count"),
                    Đúng=("is correct",lambda x:(x=="True").sum()),
                    Điểm=("score",lambda x: sum(map(float,x)))
                  ).reset_index()
            )
            stt["Chưa_trả_lời"] = tot - stt.Đã_trả_lời
            stt["Tỷ_lệ"]       = (stt.Đúng/stt.Đã_trả_lời*100).round(1)
            
            # Add export buttons for the statistics
            add_admin_export_buttons(stt)
            
            # Add ability to export individual participant results
            admin_export_participant_results()
            
            st.subheader("Thống kê Thí sinh")
            st.dataframe(stt)
            st.plotly_chart(
                px.bar(stt, x="email", y="Điểm", color="Tỷ_lệ",
                       title="Điểm của Thí sinh")
            )

    # Đổi mật khẩu
    with tab_pw:
        cur  = st.text_input("Mật khẩu hiện tại", type="password")
        new1 = st.text_input("Mật khẩu mới",      type="password")
        new2 = st.text_input("Xác nhận mật khẩu mới", type="password")
        if st.button("Đổi mật khẩu"):
            stored = gws()["admin"].cell(2,2).value
            if not verify_pw(stored,cur):
                st.error("Mật khẩu không đúng")
            elif new1!=new2:
                st.error("Mật khẩu mới không khớp")
            else:
                gws()["admin"].update("B2", [[hash_pw(new1)]])
                st.success("Đổi mật khẩu thành công")
                # ============ Trang Thí sinh ============
def page_part():
    display_logos()
    st.title(f"Chào bạn, {st.session_state.email}")
    tab_q, tab_r = st.tabs(["Làm bài","Kết quả của tôi"])

    # Chuẩn bị sheet riêng
    wb = gws()["rsp_wb"]; wn = sheet_name(st.session_state.email)
    try:
        usht = wb.worksheet(wn)
    except gspread.exceptions.WorksheetNotFound:
        usht = wb.add_worksheet(wn, rows=100, cols=26)
        usht.update("A1:E1", [
            ["Timestamp","Question ID","Selected Answers","Is Correct","Score"]
        ])
        usht.update("Z1", [["0"]])
    if usht.col_count<26:
        usht.resize(rows=usht.row_count, cols=26)
    edits = int((usht.acell("Z1").value or "0").strip())

    # Data & form
    qd = df_questions()
    if not qd.empty:
        qd["question id"]=qd["question id"].astype(int)
        qd=qd.sort_values("question id")

    # Làm bài
    with tab_q:
        if edits>=3:
            st.warning("Bạn đã đạt giới hạn 3 lần nộp.")
        else:
            raw=usht.get_all_values()[1:]
            rows=[r[:5]+[""]*(5-len(r)) for r in raw]
            my = pd.DataFrame(rows, columns=["timestamp","qid","sel","ok","score"])
            with st.form("quiz"):
                ans={}
                for _,q in qd.iterrows():
                    st.markdown(f"**Câu {q['question id']}. {q['question text']}**")
                    lines=[l for l in q['options'].splitlines() if l.strip()]
                    labs=[l.split('.')[0].strip() for l in lines]
                    prev=set()
                    rr=my[my.qid==str(q['question id'])]
                    if not rr.empty: prev=set(rr.iloc[0].sel.split(','))
                    sel=[]; cols=st.columns(len(labs))
                    for i,lab in enumerate(labs):
                        txt=lines[i][lines[i].find('.')+1:].strip() if i<len(lines) else ""
                        if cols[i].checkbox(f"{lab}. {txt}", value=lab in prev,
                                            key=f"{q['question id']}_{lab}"):
                            sel.append(lab)
                    ans[str(q['question id'])]=sel
                    st.write("---")
                sub=st.form_submit_button("Nộp bài")
            if sub:
                ts=datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                master=gws()["rsp_ws"]
                for qid,sel in ans.items():
                    if not sel: continue
                    qr=qd[qd["question id"]==int(qid)].iloc[0]
                    ok=cmp_ans(sel, qr["correct answers"].split(','))
                    sc=float(qr["points"]) if ok else 0
                    retry(lambda: master.append_row([
                        st.session_state.email,qid,",".join(sel),str(ok),
                        str(sc),ts,str(edits+1)
                    ]))
                    urow=[ts,qid,",".join(sel),str(ok),str(sc)]
                    found=usht.findall(qid)
                    if found:
                        usht.update(f"A{found[0].row}:E{found[0].row}", [urow])
                    else:
                        usht.append_row(urow)
                usht.update("Z1", [[str(edits+1)]])
                st.success("Nộp bài thành công!"); st.rerun()

    # Kết quả của tôi
    with tab_r:
        raw=usht.get_all_values()[1:]
        rows=[r[:5]+[""]*(5-len(r)) for r in raw]
        my=pd.DataFrame(rows, columns=["timestamp","qid","sel","ok","score"])
        
        # Add export buttons for the user's results
        add_export_buttons(my, email=st.session_state.email)
        
        if my.empty:
            st.info("Bạn chưa làm câu hỏi nào.")
        else:
            qd["question id"]=qd["question id"].astype(str)
            my["qid"]=my["qid"].astype(str)
            tot=len(qd); ans=len(my)
            corr=(my.ok=="True").sum()
            scr=pd.to_numeric(my.score,errors='coerce').fillna(0).sum()
            mx=pd.to_numeric(qd.points,errors='coerce').fillna(0).sum()
            c1,c2,c3,c4=st.columns(4)
            c1.metric("Đã trả lời", f"{ans}/{tot}")
            c2.metric("Đúng",        f"{corr}/{ans}")
            c3.metric("Điểm",        f"{scr}/{mx}")
            c4.metric("Lượt còn lại", f"{2-edits}")
            for _,r in my.iterrows():
                subset=qd[qd["question id"]==r.qid]
                if subset.empty:
                    with st.expander(f"{r.qid}. [Bị xóa]"):
                        st.warning("Câu hỏi đã bị xóa bởi Admin.")
                        st.write("Câu trả lời:", r.sel)
                        st.write("Thời gian:", r.timestamp)
                    continue
                qr=subset.iloc[0]
                with st.expander(f"{r.qid}. {qr['question text']}"):
                    for line in qr['options'].splitlines():
                        lab=line.split('.')[0].strip()
                        st.checkbox(line, value=lab in r.sel.split(','), disabled=True,
                                    key=f"d_{r.qid}_{lab}")
                    st.write("Đáp án đúng:", qr["correct answers"])
                    st.write("Kết quả:",     "✅" if r.ok=="True" else "❌")
                    st.write("Thời gian:",   r.timestamp)

# ----------- Router -----------
def main():
    if 'role' not in st.session_state:
        st.session_state.role = None
    if st.session_state.role is None:
        page_login()
    else:
        if st.sidebar.button("Đăng xuất"):
            st.session_state.clear(); st.rerun()
        if st.session_state.role=="admin":
            page_admin()
        else:
            page_part()

if __name__=="__main__":
    main()
                                
