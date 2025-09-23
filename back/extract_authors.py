import xml.etree.ElementTree as ET
from typing import List, Set
import requests
import gzip
import io
import html
import re

def preprocess_xml_content(content: str) -> str:
    """
    XML 내용을 전처리하여 엔티티 문제를 해결합니다.
    """
    # HTML 엔티티를 Unicode로 변환
    content = html.unescape(content)
    
    # 일반적인 XML 엔티티들을 직접 치환
    entity_replacements = {
        '&auml;': 'ä', '&ouml;': 'ö', '&uuml;': 'ü', '&Auml;': 'Ä', 
        '&Ouml;': 'Ö', '&Uuml;': 'Ü', '&szlig;': 'ß',
        '&aacute;': 'á', '&eacute;': 'é', '&iacute;': 'í', '&oacute;': 'ó', '&uacute;': 'ú',
        '&agrave;': 'à', '&egrave;': 'è', '&igrave;': 'ì', '&ograve;': 'ò', '&ugrave;': 'ù',
        '&acirc;': 'â', '&ecirc;': 'ê', '&icirc;': 'î', '&ocirc;': 'ô', '&ucirc;': 'û',
        '&atilde;': 'ã', '&ntilde;': 'ñ', '&otilde;': 'õ',
        '&aring;': 'å', '&ccedil;': 'ç', '&oslash;': 'ø',
        '&Aacute;': 'Á', '&Eacute;': 'É', '&Iacute;': 'Í', '&Oacute;': 'Ó', '&Uacute;': 'Ú',
        '&Agrave;': 'À', '&Egrave;': 'È', '&Igrave;': 'Ì', '&Ograve;': 'Ò', '&Ugrave;': 'Ù',
        '&Acirc;': 'Â', '&Ecirc;': 'Ê', '&Icirc;': 'Î', '&Ocirc;': 'Ô', '&Ucirc;': 'Û',
        '&Atilde;': 'Ã', '&Ntilde;': 'Ñ', '&Otilde;': 'Õ',
        '&Aring;': 'Å', '&Ccedil;': 'Ç', '&Oslash;': 'Ø',
        '&reg;': '®', '&micro;': 'µ', '&times;': '×'
    }
    
    for entity, char in entity_replacements.items():
        content = content.replace(entity, char)
    
    # 기타 정의되지 않은 엔티티들을 빈 문자열로 치환
    content = re.sub(r'&[a-zA-Z][a-zA-Z0-9]*;', '', content)
    
    return content

def extract_authors_from_xml_file(xml_file_path: str) -> List[str]:
    """
    로컬 XML 파일에서 모든 저자 이름을 추출합니다.
    
    Args:
        xml_file_path: XML 파일 경로
        
    Returns:
        저자 이름들의 리스트
    """
    authors = []
    
    try:
        # XML 파일을 읽고 전처리
        with open(xml_file_path, 'r', encoding='utf-8') as file:
            content = file.read()
        
        # 엔티티 문제 해결을 위한 전처리
        content = preprocess_xml_content(content)
        
        # XML 파싱
        root = ET.fromstring(content)
        
        # 모든 author 태그 찾기
        for author in root.iter('author'):
            if author.text:
                authors.append(author.text.strip())
                
    except ET.ParseError as e:
        print(f"XML 파싱 오류: {e}")
        # 다른 인코딩으로 시도
        try:
            with open(xml_file_path, 'r', encoding='latin-1') as file:
                content = file.read()
            content = preprocess_xml_content(content)
            root = ET.fromstring(content)
            for author in root.iter('author'):
                if author.text:
                    authors.append(author.text.strip())
        except Exception as e2:
            print(f"대체 인코딩 시도도 실패: {e2}")
    except FileNotFoundError:
        print(f"파일을 찾을 수 없습니다: {xml_file_path}")
    except Exception as e:
        print(f"오류 발생: {e}")
    
    return authors

def extract_unique_authors_from_xml_file(xml_file_path: str) -> List[str]:
    """
    로컬 XML 파일에서 중복을 제거한 저자 이름을 추출합니다.
    
    Args:
        xml_file_path: XML 파일 경로
        
    Returns:
        중복 제거된 저자 이름들의 리스트
    """
    authors_set = set()
    
    try:
        with open(xml_file_path, 'r', encoding='utf-8') as file:
            content = file.read()
        
        content = preprocess_xml_content(content)
        root = ET.fromstring(content)
        
        for author in root.iter('author'):
            if author.text:
                authors_set.add(author.text.strip())
                
    except ET.ParseError as e:
        print(f"XML 파싱 오류: {e}")
        try:
            with open(xml_file_path, 'r', encoding='latin-1') as file:
                content = file.read()
            content = preprocess_xml_content(content)
            root = ET.fromstring(content)
            for author in root.iter('author'):
                if author.text:
                    authors_set.add(author.text.strip())
        except Exception as e2:
            print(f"대체 인코딩 시도도 실패: {e2}")
    except FileNotFoundError:
        print(f"파일을 찾을 수 없습니다: {xml_file_path}")
    except Exception as e:
        print(f"오류 발생: {e}")
    
    return sorted(list(authors_set))

def extract_authors_iteratively(xml_file_path: str, max_authors: int = None) -> List[str]:
    """
    큰 XML 파일을 메모리 효율적으로 처리하여 저자를 추출합니다.
    
    Args:
        xml_file_path: XML 파일 경로
        max_authors: 추출할 최대 저자 수 (None이면 제한 없음)
        
    Returns:
        저자 이름들의 리스트
    """
    authors = []
    author_count = 0
    
    try:
        # 파일을 스트리밍으로 읽기
        with open(xml_file_path, 'r', encoding='utf-8') as file:
            # 간단한 정규식으로 author 태그 찾기
            for line_num, line in enumerate(file, 1):
                # <author>...</author> 패턴 찾기
                author_matches = re.findall(r'<author[^>]*>(.*?)</author>', line)
                for match in author_matches:
                    # HTML 엔티티 디코딩
                    author_name = html.unescape(match.strip())
                    if author_name:
                        authors.append(author_name)
                        author_count += 1
                        
                        if max_authors and author_count >= max_authors:
                            print(f"{max_authors}명 제한에 도달했습니다.")
                            return authors
                
                # 진행 상황 표시
                if line_num % 10000 == 0:
                    print(f"처리된 라인: {line_num}, 찾은 저자: {author_count}")
                    
    except FileNotFoundError:
        print(f"파일을 찾을 수 없습니다: {xml_file_path}")
    except Exception as e:
        print(f"오류 발생: {e}")
        # UTF-8로 안 되면 다른 인코딩 시도
        try:
            with open(xml_file_path, 'r', encoding='latin-1') as file:
                for line_num, line in enumerate(file, 1):
                    author_matches = re.findall(r'<author[^>]*>(.*?)</author>', line)
                    for match in author_matches:
                        author_name = html.unescape(match.strip())
                        if author_name:
                            authors.append(author_name)
                            author_count += 1
                            
                            if max_authors and author_count >= max_authors:
                                return authors
                    
                    if line_num % 10000 == 0:
                        print(f"처리된 라인: {line_num}, 찾은 저자: {author_count}")
        except Exception as e2:
            print(f"대체 인코딩도 실패: {e2}")
    
    return authors
def extract_authors_from_xml_string(xml_string: str) -> List[str]:
    """
    XML 문자열에서 모든 저자 이름을 추출합니다.
    
    Args:
        xml_string: XML 문자열
        
    Returns:
        저자 이름들의 리스트
    """
    authors = []
    
    try:
        # 전처리
        xml_string = preprocess_xml_content(xml_string)
        root = ET.fromstring(xml_string)
        
        for author in root.iter('author'):
            if author.text:
                authors.append(author.text.strip())
                
    except ET.ParseError as e:
        print(f"XML 파싱 오류: {e}")
        # 정규식으로 대체 시도
        author_matches = re.findall(r'<author[^>]*>(.*?)</author>', xml_string)
        for match in author_matches:
            author_name = html.unescape(match.strip())
            if author_name:
                authors.append(author_name)
    except Exception as e:
        print(f"오류 발생: {e}")
    
    return authors

def download_and_extract_authors_from_dblp(limit: int = 1000) -> List[str]:
    """
    DBLP XML 덤프를 다운로드하여 저자를 추출합니다. (메모리 효율적)
    
    Args:
        limit: 처리할 최대 저자 수 (메모리 절약을 위함)
        
    Returns:
        저자 이름들의 리스트
    """
    url = "https://dblp.org/xml/dblp.xml.gz"
    authors = []
    author_count = 0
    
    try:
        print("DBLP XML 덤프 다운로드 중...")
        response = requests.get(url, stream=True)
        response.raise_for_status()
        
        # gzip 압축 해제
        with gzip.GzipFile(fileobj=io.BytesIO(response.content)) as gz_file:
            print("XML 파싱 및 저자 추출 중...")
            
            # iterparse를 사용하여 메모리 효율적으로 처리
            for event, elem in ET.iterparse(gz_file, events=('start', 'end')):
                if event == 'end' and elem.tag == 'author':
                    if elem.text and author_count < limit:
                        authors.append(elem.text.strip())
                        author_count += 1
                    # 메모리 절약을 위해 처리된 요소 제거
                    elem.clear()
                    
                if author_count >= limit:
                    break
                    
    except requests.RequestException as e:
        print(f"다운로드 오류: {e}")
    except ET.ParseError as e:
        print(f"XML 파싱 오류: {e}")
    except Exception as e:
        print(f"오류 발생: {e}")
    
    return authors

def get_author_statistics(authors: List[str]) -> dict:
    """
    저자 목록의 통계 정보를 반환합니다.
    
    Args:
        authors: 저자 이름들의 리스트
        
    Returns:
        통계 정보 딕셔너리
    """
    from collections import Counter
    
    author_counter = Counter(authors)
    
    return {
        'total_authors': len(authors),
        'unique_authors': len(set(authors)),
        'most_frequent_authors': author_counter.most_common(10),
        'authors_with_single_paper': sum(1 for count in author_counter.values() if count == 1)
    }

# 사용 예시
if __name__ == "__main__":
    # 방법 1: 메모리 효율적인 반복 처리 (권장)
    print("=== 메모리 효율적인 방법으로 저자 추출 ===")
    authors = extract_authors_iteratively("dblp.xml")  # 1000명으로 제한
    print(f"총 {len(authors)}명의 저자를 찾았습니다.")
    authors = list(set(authors))  # 중복 제거
    
    import json
    with open("all_names.json", "w", encoding="utf-8") as f:
        json.dump(authors, f, ensure_ascii=False, indent=2)
    
    # 방법 2: 전체 파일 로드 (작은 파일용)
    # print("\n=== 전체 파일 로드 방법 ===")
    # authors = extract_authors_from_xml_file("dblp.xml")
    # print(f"총 {len(authors)}명의 저자를 찾았습니다.")
    # print("처음 10명의 저자:", authors[:10])
    
    # 방법 3: 중복 제거된 저자 목록
    # unique_authors = extract_unique_authors_from_xml_file("dblp.xml")
    # print(f"중복 제거 후 {len(unique_authors)}명의 저자:")
    # print("처음 10명:", unique_authors[:10])
    
    # 방법 4: DBLP에서 직접 다운로드 (제한된 수만)
    # authors = download_and_extract_authors_from_dblp(limit=1000)
    # print(f"다운로드하여 {len(authors)}명의 저자를 추출했습니다.")
    # if authors:
    #     print("처음 10명의 저자:", authors[:10])
        
    #     # 통계 정보 출력
    #     stats = get_author_statistics(authors)
    #     print(f"\n통계 정보:")
    #     print(f"전체 저자 수: {stats['total_authors']}")
    #     print(f"고유 저자 수: {stats['unique_authors']}")
    #     print(f"논문 1편인 저자 수: {stats['authors_with_single_paper']}")
    #     print(f"가장 많은 논문을 쓴 저자들:")
    #     for author, count in stats['most_frequent_authors'][:5]:
    #         print(f"  {author}: {count}편")
    
    # # 방법 5: XML 문자열에서 직접 추출 (작은 샘플용)
    # print("\n=== 샘플 XML 테스트 ===")
    # sample_xml = """<?xml version="1.0" encoding="UTF-8"?>
    # <dblp>
    #     <article key="journals/ai/Smith2023">
    #         <author>John Smith</author>
    #         <author>Jane D&ouml;e</author>
    #         <author>M&uuml;ller</author>
    #         <title>Machine Learning Applications</title>
    #         <year>2023</year>
    #     </article>
    #     <inproceedings key="conf/icml/Johnson2022">
    #         <author>Bob Johnson</author>
    #         <author>John Smith</author>
    #         <title>Deep Learning Methods</title>
    #         <year>2022</year>
    #     </inproceedings>
    # </dblp>"""
    
    # sample_authors = extract_authors_from_xml_string(sample_xml)
    # print(f"샘플 XML에서 추출한 저자: {sample_authors}")
    
    # if authors:
    #     # 통계 정보 출력
    #     stats = get_author_statistics(authors)
    #     print(f"\n=== 통계 정보 ===")
    #     print(f"전체 저자 수: {stats['total_authors']}")
    #     print(f"고유 저자 수: {stats['unique_authors']}")
    #     print(f"논문 1편인 저자 수: {stats['authors_with_single_paper']}")
    #     print(f"가장 많은 논문을 쓴 저자들:")
    #     for author, count in stats['most_frequent_authors'][:5]:
    #         print(f"  {author}: {count}편")