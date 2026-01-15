from back.pcss import PCSSEARCH
import pandas as pd
import os
import gspread
from google.oauth2.service_account import Credentials
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication
import smtplib
from docx import Document
from docx.shared import Pt, RGBColor
from docx.oxml import parse_xml
from docx.oxml.ns import nsdecls, qn
from datetime import datetime
import json
import re

class AutoSender:
    
    def __init__(self):

        self.test = True
        self.target_year = 2024
        
        self.period_day = 7
        self.period_second = self.period_day * 24 * 60 * 60
        self.period_second = 30
        
        self.CrawlOption = 1
        
        self.spreadsheet_url = "https://docs.google.com/spreadsheets/d/1SsGBT17nzA9ItG8QG73lyHGeIab2C6x616zYwEATQHc/edit?gid=0#gid=0"
        self.user_history_path = os.path.join(os.path.dirname(__file__), 'user_history')
        self.conf_df = pd.read_csv(os.path.join(os.path.dirname(__file__), 'data', 'conf.csv'))
        
        
    def get_spreadsheet_data(self, sheet="Sheet1"):
        
        test_data = [
            {
                'Email': 'moonyojun@naver.com',
                'Networks': "TRUE",
                'Security & Cryptography': "FALSE",
                'Computer Vision & Graphics': "FALSE",
                'Data Mining & Information Retrieval': "FALSE",
                'Machine Learning': "FALSE",
                'Theory': "FALSE",
                'Human-Computer Interaction': "FALSE",
                'Linguistics & Speech': "FALSE",
                'Operating Systems': "FALSE",
                'Computer Architecture': "FALSE",
                'Databases': "FALSE",
                'Programming Languages\r': "FALSE",  # '\r'이 포함된 경우 주의
                'Software Engineering': "FALSE",
                'Embedded & Real-Time Systems': "FALSE",
                'High-Performance Computing': "FALSE",
                'Mobile Computing': "FALSE",
                'Robotics': "FALSE",
                'Artificial Intelligence': "FALSE",
                'Computational Biology': "FALSE",
                'Computational Economics': "FALSE",
                'Design Automation': "FALSE",
                'Visualization': "FALSE"
            },
        ]

        # Google Spreadsheet API 인증
        json_file_path = os.path.join(os.path.dirname(__file__), 'data', "lock.json")
        scopes = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]

        credentials = Credentials.from_service_account_file(json_file_path, scopes=scopes)
        gc = gspread.authorize(credentials)

        # Google Spreadsheet 열기
        spreadsheet_url = self.spreadsheet_url
        doc = gc.open_by_url(spreadsheet_url)

        # 특정 워크시트 선택
        worksheet = doc.worksheet(sheet)

        # 데이터를 가져와 DataFrame으로 변환
        data = worksheet.get_all_values()
        df = pd.DataFrame(data[1:], columns=data[0])  # 첫 번째 행을 컬럼으로 설정
        
        dict_list = df.to_dict(orient='records')
        if self.test == True:
            dict_list = test_data
        new_dict_list = []
        
        for dict in dict_list:
            new_dict = {}
            true_list = []
            for key, value in dict.items():
                if value == 'TRUE':
                    new_dict[key] = True
                    true_list.append(key)
                elif value == 'FALSE':
                    new_dict[key] = False
                else:
                    new_dict[key] = value
            filtered_df = self.conf_df[self.conf_df['kind'].isin(true_list)]
            conf_list = filtered_df['conference'].tolist()
            
            new_dict['conf_list'] = conf_list
            new_dict['kind_list'] = true_list
            new_dict_list.append(new_dict) 
        
        return new_dict_list
    
    def send_email(self, receiver, title, text, file_path=None):
        mail_json = os.path.join(os.path.dirname(__file__), 'data', "mail_lock.json")
        
        # JSON 파일에서 메일 계정 정보 읽기
        with open(mail_json, 'r', encoding='utf-8') as jsonfile:
            mail_data = json.load(jsonfile)

        sender = mail_data['sender']
        MailPassword = mail_data['password']

        # 이메일 메시지 생성
        msg = MIMEMultipart()
        msg['Subject'] = title
        msg['From'] = sender
        msg['To'] = receiver

        # 이메일 본문 추가
        msg.attach(MIMEText(text, 'plain'))

        # 파일 첨부 (file_path가 제공된 경우)
        if file_path and os.path.exists(file_path):
            with open(file_path, 'rb') as f:
                file_attachment = MIMEApplication(f.read(), Name=os.path.basename(file_path))
                file_attachment['Content-Disposition'] = f'attachment; filename="{os.path.basename(file_path)}"'
                msg.attach(file_attachment)

        smtp_server = "smtp.gmail.com"
        smtp_port = 587

        # SMTP 연결 및 메일 보내기
        with smtplib.SMTP(smtp_server, smtp_port) as server:
            server.starttls()
            server.login(sender, MailPassword)
            server.sendmail(sender, receiver, msg.as_string())
    
    def main(self):
        while True:
            self.auto_send()
            break
    
    def auto_send(self):
        UserDataList = self.get_spreadsheet_data()
        for UserData in UserDataList:
            conf_list = UserData['conf_list']

            pcssearch_obj = PCSSEARCH(self.CrawlOption, False, self.target_year, self.target_year)
            result_json_path = pcssearch_obj.main(conf_list)
            if os.path.exists(result_json_path):
                os.remove(result_json_path)

            FinalData = pcssearch_obj.FinalData
            
            print(f"{UserData['Email']} 크롤링 완료")
            
            historyData = self.manage_history(self.user_history_path, UserData['Email'], option="GET")
            if historyData != {}:
                titles = [entry["title"] for entry in historyData.values() if "title" in entry]
                
                FilteredData = [entry for entry in FinalData.values() if "title" in entry and entry["title"] not in titles]
                FilteredData = {str(i): entry for i, entry in enumerate(FilteredData)}
            else:
                FilteredData = FinalData
            
            mergedData = list(FinalData.values()) + list(FilteredData.values())
            reindexed_data = {str(i): entry for i, entry in enumerate(mergedData)}                             # history 갱신
            
            self.manage_history(self.user_history_path, UserData['Email'], option='SAVE', data=reindexed_data) # 기록 저장
            docx_file = self.make_report(self.user_history_path, FilteredData, UserData)

            self.send_email(
                receiver = UserData['Email'],
                title = f"{datetime.now().strftime("%Y-%m-%d")} PCSS Subscribe Summary",
                text = f'Subscribe => {', '.join(UserData['kind_list'])}',
                file_path = docx_file
            )
            os.remove(docx_file)
            print(f"{UserData['Email']} 데이터 저장 및 전송 완료")


    def manage_history(self, folder_path, email, option, data = None):
        # 파일명에 안전한 문자만 사용 (이메일에서 파일명 불가능한 문자 제거)
        safe_email = re.sub(r'[^\w\.-]', '_', email)
        json_filename = f"{safe_email}.json"
        json_filepath = os.path.join(folder_path, json_filename)

        if option == 'GET':
            # 파일이 존재하는 경우: JSON 불러오기
            if os.path.exists(json_filepath):
                with open(json_filepath, 'r', encoding='utf-8') as f:
                    json_data = json.load(f)
                return json_data
            
            # 파일이 없는 경우: 새 JSON 파일 생성
            else:
                return {}
        elif option == "SAVE":
            with open(json_filepath, "w", encoding="utf-8") as file:
                json.dump(data, file, indent=4, ensure_ascii=False)

    def make_report(self, folder_path, data, UserData):
        df = self.conf_df
        conference_to_kind = dict(zip(df["conference"], df["kind"]))

        current_date = datetime.now().strftime("%Y%m%d")
        safe_email = re.sub(r'[^\w\.-]', '_', UserData['Email'])
        docx_filename = f"{current_date}_summary.docx"

        for entry in data.values():
            entry["kind"] = conference_to_kind.get(entry["conference"], "Unknown")

        # Kind별로 데이터 정리
        grouped_by_kind = {}
        for entry in data.values():
            kind = entry["kind"]
            if kind not in grouped_by_kind:
                grouped_by_kind[kind] = {}
            conf = entry["conference"]
            if conf not in grouped_by_kind[kind]:
                grouped_by_kind[kind][conf] = []
            grouped_by_kind[kind][conf].append(entry)


        doc = self.make_docx(grouped_by_kind, UserData)
        # Word 문서 저장
        doc_path = os.path.join(folder_path, docx_filename)
        doc.save(doc_path)
        return doc_path

    def add_hyperlink(self, paragraph, text, url):
        """
        주어진 문단(paragraph)에 하이퍼링크를 추가하는 함수
        """
        part = paragraph._parent.part
        r_id = part.relate_to(url, "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink",
                              is_external=True)

        # ✅ w 네임스페이스를 명확하게 추가
        hyperlink = parse_xml(
            f'<w:hyperlink r:id="{r_id}" {nsdecls("w", "r")}>'  # ✅ 네임스페이스 추가
            f'<w:r><w:rPr><w:color w:val="0000FF"/><w:u w:val="single"/></w:rPr>'
            f'<w:t>{text}</w:t></w:r></w:hyperlink>'
        )

        paragraph._element.append(hyperlink)

    def make_docx(self, grouped_by_kind, UserData):
        doc = Document()
        font_name = "맑은 고딕"

        styles = doc.styles
        for style_name in ["Normal", "Heading1", "Heading2", "Heading3", "Table Grid"]:
            if style_name in styles:
                style = styles[style_name]
                # 한글이 올바르게 표시되도록 설정
                style.font.name = font_name
                style.font.element.rPr.rFonts.set(qn('w:eastAsia'), font_name)

                if style_name == "Normal":
                    style.font.size = Pt(11)
                    style.font.color.rgb = RGBColor(0, 0, 0)
                    style.paragraph_format.space_after = Pt(6)
                elif style_name == "Heading1":
                    style.font.size = Pt(22)
                    style.font.bold = True
                    style.font.color.rgb = RGBColor(0xC8, 0x01, 0x50)  # POSTECH Red
                    style.paragraph_format.space_before = Pt(12)
                    style.paragraph_format.space_after = Pt(6)
                elif style_name == "Heading2":
                    style.font.size = Pt(18)
                    style.font.bold = True
                    style.font.color.rgb = RGBColor(0x00, 0x4F, 0x99)  # 세련된 블루 톤
                    style.paragraph_format.space_before = Pt(10)
                    style.paragraph_format.space_after = Pt(4)
                elif style_name == "Heading3":
                    style.font.size = Pt(16)
                    style.font.bold = True
                    style.font.color.rgb = RGBColor(0x00, 0x80, 0x00)  # 세련된 그린 톤
                    style.paragraph_format.space_before = Pt(8)
                    style.paragraph_format.space_after = Pt(4)
                elif style_name == "Table Grid":
                    style.font.size = Pt(10)
                    style.font.color.rgb = RGBColor(0, 0, 0)
                    style.paragraph_format.space_after = Pt(4)

        doc.add_heading(f"{datetime.now().strftime('%Y-%m-%d')} Conference Summary", level=1)

        doc.add_paragraph(f"- 수신인: {UserData['Email']}")
        doc.add_paragraph(f"- 구독 대상: {', '.join(UserData['kind_list'])}")
        doc.add_paragraph(f"- 크롤링 대상 연도: {self.target_year}")

        # Kind -> Conference 순서로 문서 작성
        for kind, conferences in grouped_by_kind.items():
            doc.add_heading(f"📌 {kind}", level=1)

            for conf, entries in conferences.items():
                doc.add_heading(f"학회: {conf}", level=2)
                doc.add_heading(f"출처: {entries[0]['source']}", level=3)

                # 테이블 생성 (열: Target Author, Title, Authors)
                table = doc.add_table(rows=1, cols=3)
                table.style = "Table Grid"

                # 테이블 헤더 설정
                hdr_cells = table.rows[0].cells
                hdr_cells[0].text = "1저자"
                hdr_cells[1].text = "제목"
                hdr_cells[2].text = "저자 목록"

                # 헤더 폰트 서식 개선 (예시: 흰색 볼드 글씨)
                for cell in hdr_cells:
                    for paragraph in cell.paragraphs:
                        for run in paragraph.runs:
                            run.font.bold = True
                            run.font.color.rgb = RGBColor(255, 255, 255)
                    # (테이블 셀 배경색 적용은 XML 조작이 필요하므로 생략)

                for entry in entries:
                    row_cells = table.add_row().cells
                    row_cells[0].text = ", ".join(entry["target_author"]) if entry["target_author"] else "N/A"
                    row_cells[1].text = entry["title"]

                    # 저자 이름과 URL을 매칭하여 추가 (하이퍼링크 포함)
                    p = row_cells[2].paragraphs[0]
                    for name, url in zip(entry["author_name"], entry["author_url"]):
                        self.add_hyperlink(p, name, url)
                        p.add_run("\n")

                # 테이블과 다음 내용 사이 간격 추가
                doc.add_paragraph("\n")

        return doc


if __name__ == '__main__':
    autoSender_obj = AutoSender()
    autoSender_obj.main()