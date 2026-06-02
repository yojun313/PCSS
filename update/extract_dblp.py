import os
import sys
import gzip
import requests
from app.db import dblp_col
from lxml import etree
from pymongo import MongoClient
from pathlib import Path
from rich.console import Console
from rich.progress import (
    Progress,
    SpinnerColumn,
    TextColumn,
    BarColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
    DownloadColumn,
    TransferSpeedColumn,
)


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
XML_FILENAME = "dblp.xml"
DTD_FILENAME = "dblp.dtd"

BATCH_SIZE = 2000

console = Console()


def elem_to_dict(elem):
    data = dict(elem.attrib)
    data["type"] = elem.tag

    for child in elem:
        tag = child.tag

        # 1. 텍스트 추출 방식 변경: 하위 태그(<i>, <sup> 등) 포함 모든 텍스트 병합
        text = "".join(child.itertext()).strip()

        # 2. 속성 처리: 텍스트가 없어도 속성이 있으면 저장해야 함
        if child.attrib:
            value = dict(child.attrib)
            if text:
                value["text"] = text
        else:
            value = text

        # 텍스트도 없고 속성도 없으면 건너뜀 (빈 태그)
        if not value and value != "":
            continue

        if tag in data:
            if isinstance(data[tag], list):
                data[tag].append(value)
            else:
                data[tag] = [data[tag], value]
        else:
            # 리스트로 저장할 필드들은 여기서 정의
            if tag in ["author", "editor", "cite", "cdrom", "url", "crossref"]:
                data[tag] = [value]
            else:
                data[tag] = value

    return data


def download_dblp_assets(out_dir):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    files = {
        "dblp.xml.gz": "https://dblp.uni-trier.de/xml/dblp.xml.gz",
        "dblp.dtd": "https://dblp.uni-trier.de/xml/dblp.dtd",
    }

    for filename, url in files.items():
        file_path = out_dir / filename
        if file_path.exists():
            continue

        with requests.get(url, stream=True) as r:
            r.raise_for_status()
            total_size = int(r.headers.get("Content-Length", 0))

            with Progress(
                TextColumn(f"[bold blue]Downloading {filename}"),
                BarColumn(),
                DownloadColumn(),
                TransferSpeedColumn(),
                TimeRemainingColumn(),
                console=console,
            ) as progress:
                task = progress.add_task("Download", total=total_size)
                with open(file_path, "wb") as f:
                    for chunk in r.iter_content(chunk_size=1024 * 1024):
                        if chunk:
                            f.write(chunk)
                            progress.update(task, advance=len(chunk))

    xml_path = out_dir / XML_FILENAME
    gz_path = out_dir / "dblp.xml.gz"

    if not xml_path.exists() and gz_path.exists():
        console.print("[bold blue]Extracting dblp.xml.gz...[/bold blue]")
        with gzip.open(gz_path, "rb") as f_in, open(xml_path, "wb") as f_out:
            while True:
                chunk = f_in.read(1024 * 1024)
                if not chunk:
                    break
                f_out.write(chunk)
        console.print("[bold green]Extraction Complete[/bold green]")


def parse_and_save(xml_path):
    col = dblp_col
    col.delete_many({})

    xml_dir = os.path.dirname(xml_path)
    dtd_path = os.path.join(xml_dir, DTD_FILENAME)

    try:
        etree.DTD(file=dtd_path)
    except Exception:
        console.print("[bold red]DTD Error[/bold red]: Proceeding without validation")

    target_tags = {
        "article",
        "inproceedings",
        "proceedings",
        "book",
        "incollection",
        "phdthesis",
        "mastersthesis",
        "www",
        "data",
    }

    context = etree.iterparse(
        xml_path, events=("end",), load_dtd=True, huge_tree=True, recover=True
    )

    batch = []
    count = 0

    with Progress(
        SpinnerColumn(),
        TextColumn("[bold green]Processing DBLP XML..."),
        BarColumn(),
        TextColumn("{task.completed} records"),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("Parse", total=None)

        for event, elem in context:
            if elem.tag in target_tags:
                doc = elem_to_dict(elem)
                batch.append(doc)
                count += 1

                if len(batch) >= BATCH_SIZE:
                    col.insert_many(batch, ordered=False)
                    batch = []
                    progress.update(task, completed=count)

                elem.clear()
                while elem.getprevious() is not None:
                    del elem.getparent()[0]

            elif elem.tag == "dblp":
                elem.clear()

        if batch:
            col.insert_many(batch, ordered=False)
            progress.update(task, completed=count)

    console.print(f"[bold green]Done![/bold green] Total {count} records saved.")


if __name__ == "__main__":
    if not os.path.exists(os.path.join(BASE_DIR, XML_FILENAME)):
        download_dblp_assets(BASE_DIR)
    xml_full_path = os.path.join(BASE_DIR, XML_FILENAME)
    parse_and_save(xml_full_path)
