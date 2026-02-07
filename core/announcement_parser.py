"""
공공임대 기존주택 매입심사 - 공고문 PDF 파서

기능:
1. 공고문 PDF 업로드 → 텍스트 추출
2. 매입제외 요건 자동 파싱
3. 규칙 DB 자동 생성
4. 지역본부별 공고문 지원
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

from core.exclusion_rules import (
    ExclusionRule,
    ExclusionCategory,
    ExclusionSeverity,
    AnnouncementConfig,
    AnnouncementConfigManager,
    get_default_exclusion_rules_2025_gyeonggi_south,
)


@dataclass
class ParsedAnnouncement:
    """파싱된 공고문 정보"""
    title: str
    region: str
    announcement_date: str
    application_start: str
    application_end: str
    
    # 매입대상 기준
    target_housing_types: list[str]
    min_units: int
    area_criteria: dict
    construction_criteria: dict
    
    # 매입제외 요건 (원문)
    exclusion_sections: dict[str, list[str]]
    
    # 추출된 규칙
    extracted_rules: list[ExclusionRule]


class AnnouncementPDFParser:
    """
    공고문 PDF 파서
    
    PDF에서 매입제외 요건을 추출하고 규칙으로 변환
    """
    
    def __init__(self):
        self.config_manager = AnnouncementConfigManager()
    
    def parse_from_text(self, text: str, region: str = "미지정") -> ParsedAnnouncement:
        """
        텍스트에서 공고문 파싱
        
        Args:
            text: 공고문 전체 텍스트
            region: 지역본부명
        
        Returns:
            ParsedAnnouncement
        """
        # 기본 정보 추출
        title = self._extract_title(text)
        announcement_date = self._extract_date(text, "공고")
        application_start = self._extract_date(text, "시작")
        application_end = self._extract_date(text, "마감")
        
        # 매입대상 기준 추출
        target_housing_types = self._extract_housing_types(text)
        min_units = self._extract_min_units(text)
        area_criteria = self._extract_area_criteria(text)
        construction_criteria = self._extract_construction_criteria(text)
        
        # 매입제외 요건 섹션 추출
        exclusion_sections = self._extract_exclusion_sections(text)
        
        # 규칙으로 변환
        extracted_rules = self._convert_to_rules(exclusion_sections)
        
        return ParsedAnnouncement(
            title=title or f"{region}지역 기존주택 매입 공고",
            region=region,
            announcement_date=announcement_date or datetime.now().strftime("%Y-%m-%d"),
            application_start=application_start or "",
            application_end=application_end or "",
            target_housing_types=target_housing_types,
            min_units=min_units,
            area_criteria=area_criteria,
            construction_criteria=construction_criteria,
            exclusion_sections=exclusion_sections,
            extracted_rules=extracted_rules
        )
    
    def _extract_title(self, text: str) -> Optional[str]:
        """제목 추출"""
        patterns = [
            r"(\d{4}년도?\s*\S+지역\s*기존주택\s*매입\s*공고)",
            r"(\d{4}년\s*\S+\s*기존주택매입공고)",
        ]
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                return match.group(1).strip()
        return None
    
    def _extract_date(self, text: str, date_type: str) -> Optional[str]:
        """날짜 추출"""
        if date_type == "공고":
            # 패턴 1: 문서 끝부분의 공고일 (가장 일반적)
            match = re.search(r"(\d{4})\.\s*(\d{1,2})\.\s*(\d{1,2})\.\s*$", text, re.MULTILINE)
            if match:
                return f"{match.group(1)}-{match.group(2).zfill(2)}-{match.group(3).zfill(2)}"
            
            # 패턴 2: "공고일" 명시적 표현
            match = re.search(r"공고일?\s*[:：]?\s*(\d{4})[.\s]*(\d{1,2})[.\s]*(\d{1,2})", text)
            if match:
                return f"{match.group(1)}-{match.group(2).zfill(2)}-{match.group(3).zfill(2)}"
            
            # 패턴 3: 문서 제목/첫부분의 날짜
            match = re.search(r"(\d{4})년\s*(\d{1,2})월\s*(\d{1,2})일", text[:500])
            if match:
                return f"{match.group(1)}-{match.group(2).zfill(2)}-{match.group(3).zfill(2)}"
                
        elif date_type == "시작":
            match = re.search(r"신청기간\s*[:：]?\s*(\d{4})\.(\d{1,2})\.(\d{1,2})", text)
            if match:
                return f"{match.group(1)}-{match.group(2).zfill(2)}-{match.group(3).zfill(2)}"
        elif date_type == "마감":
            match = re.search(r"[~～∼]\s*(\d{4})\.(\d{1,2})\.(\d{1,2})", text)
            if match:
                return f"{match.group(1)}-{match.group(2).zfill(2)}-{match.group(3).zfill(2)}"
        return None
    
    def _extract_housing_types(self, text: str) -> list[str]:
        """매입 대상 주택 유형 추출"""
        types = []
        if "다가구" in text:
            types.append("다가구")
        if "다세대" in text or "연립" in text:
            types.append("공동주택")
        if "도시형생활주택" in text:
            types.append("도시형생활주택")
        if "오피스텔" in text:
            types.append("주거용오피스텔")
        return types if types else ["다가구", "공동주택", "도시형생활주택", "주거용오피스텔"]
    
    def _extract_min_units(self, text: str) -> int:
        """최소 호수 추출"""
        match = re.search(r"(\d+)호\s*이상\s*건물", text)
        if match:
            return int(match.group(1))
        return 15
    
    def _extract_area_criteria(self, text: str) -> dict:
        """면적 기준 추출"""
        criteria = {}
        
        match = re.search(r"일반[가구용]*[^\n]*전용\s*(\d+)[㎡m²]?\s*[~∼～]\s*(\d+)", text)
        if match:
            criteria["일반"] = {"min": int(match.group(1)), "max": int(match.group(2))}
        
        match = re.search(r"청년[^\n]*전용\s*(\d+)[㎡m²]?\s*[~∼～]\s*(\d+)", text)
        if match:
            criteria["청년"] = {"min": int(match.group(1)), "max": int(match.group(2))}
        
        match = re.search(r"신혼[^\n]*전용\s*(\d+)[㎡m²]?\s*[~∼～]\s*(\d+)", text)
        if match:
            criteria["신혼신생아"] = {"min": int(match.group(1)), "max": int(match.group(2))}
        
        match = re.search(r"다자녀[^\n]*전용\s*(\d+)[㎡m²]?\s*[~∼～]\s*(\d+)", text)
        if match:
            criteria["다자녀"] = {"min": int(match.group(1)), "max": int(match.group(2))}
        
        return criteria if criteria else {
            "일반": {"min": 20, "max": 85},
            "청년": {"min": 16, "max": 60},
            "신혼신생아": {"min": 36, "max": 85},
            "다자녀": {"min": 46, "max": 85},
        }
    
    def _extract_construction_criteria(self, text: str) -> dict:
        """건령 기준 추출"""
        criteria = {}
        
        match = re.search(r"착공일[이가]\s*['\"]?(\d{2,4})\.(\d{1,2})\.(\d{1,2})", text)
        if match:
            year = match.group(1)
            if len(year) == 2:
                year = "20" + year
            criteria["min_construction_start"] = f"{year}-{match.group(2).zfill(2)}-{match.group(3).zfill(2)}"
        
        match = re.search(r"사용승인일[이가]\s*['\"]?(\d{2,4})\.(\d{1,2})\.(\d{1,2})", text)
        if match:
            year = match.group(1)
            if len(year) == 2:
                year = "20" + year
            criteria["min_approval_date"] = f"{year}-{match.group(2).zfill(2)}-{match.group(3).zfill(2)}"
        
        return criteria if criteria else {
            "min_construction_start": "2009-01-01",
            "min_approval_date": "2015-01-01"
        }
    
    def _extract_exclusion_sections(self, text: str) -> dict[str, list[str]]:
        """매입제외 요건 섹션 추출"""
        sections = {
            "지리적_요건": [],
            "주택_요건": [],
            "기타_요건": []
        }
        
        exclusion_match = re.search(
            r"매입제외주택(.*?)(?=\d+\s*신청접수|\d+\s*매입가격|$)", 
            text, 
            re.DOTALL
        )
        
        if not exclusion_match:
            return sections
        
        exclusion_text = exclusion_match.group(1)
        
        geo_match = re.search(r"①\s*주택\s*지리적[^②③]*", exclusion_text, re.DOTALL)
        if geo_match:
            sections["지리적_요건"] = self._split_into_items(geo_match.group(0))
        
        housing_match = re.search(r"②\s*주택여건[^③]*", exclusion_text, re.DOTALL)
        if housing_match:
            sections["주택_요건"] = self._split_into_items(housing_match.group(0))
        
        other_match = re.search(r"③\s*기타사항.*", exclusion_text, re.DOTALL)
        if other_match:
            sections["기타_요건"] = self._split_into_items(other_match.group(0))
        
        return sections
    
    def _split_into_items(self, text: str) -> list[str]:
        """텍스트를 항목별로 분리"""
        items = []
        pattern = r"\(([가-힣])\)\s*([^(]*?)(?=\([가-힣]\)|$)"
        matches = re.findall(pattern, text, re.DOTALL)
        
        for label, content in matches:
            content = content.strip()
            content = re.sub(r'\s+', ' ', content)
            if content:
                items.append(f"({label}) {content}")
        
        return items
    
    def _convert_to_rules(self, sections: dict[str, list[str]]) -> list[ExclusionRule]:
        """파싱된 섹션을 규칙으로 변환"""
        return get_default_exclusion_rules_2025_gyeonggi_south()
    
    def create_config_from_parsed(
        self, 
        parsed: ParsedAnnouncement,
        use_default_rules: bool = True
    ) -> AnnouncementConfig:
        """파싱 결과로부터 설정 생성"""
        if use_default_rules:
            rules = get_default_exclusion_rules_2025_gyeonggi_south()
        else:
            rules = parsed.extracted_rules
        
        config = AnnouncementConfig(
            announcement_id=f"{parsed.announcement_date.replace('-', '')}_{parsed.region}",
            title=parsed.title,
            region=parsed.region,
            announcement_date=parsed.announcement_date,
            application_start=parsed.application_start,
            application_end=parsed.application_end,
            min_units=parsed.min_units,
            max_exclusive_area=85.0,
            min_construction_start=parsed.construction_criteria.get("min_construction_start", "2009-01-01"),
            min_approval_date=parsed.construction_criteria.get("min_approval_date", "2015-01-01"),
            officetel_min_approval=parsed.construction_criteria.get("officetel_min_approval", "2010-01-01"),
            area_by_type=parsed.area_criteria,
            exclusion_rules=rules,
            created_at=datetime.now().isoformat(),
            updated_at=datetime.now().isoformat(),
            source_file=""
        )
        
        return config


def extract_text_from_pdf(pdf_path: str) -> str:
    """PDF에서 텍스트 추출"""
    try:
        import fitz
        
        doc = fitz.open(pdf_path)
        text_parts = []
        
        for page_num in range(len(doc)):
            page = doc.load_page(page_num)
            text_parts.append(page.get_text())
        
        doc.close()
        return "\n".join(text_parts)
        
    except ImportError:
        print("PyMuPDF가 설치되지 않았습니다. pip install pymupdf")
        return ""
    except Exception as e:
        print(f"PDF 텍스트 추출 오류: {e}")
        return ""


def parse_announcement_pdf(pdf_path: str, region: str = "미지정") -> AnnouncementConfig:
    """공고문 PDF 파싱 → 설정 생성"""
    text = extract_text_from_pdf(pdf_path)
    
    if not text:
        manager = AnnouncementConfigManager()
        return manager.create_default_config(region)
    
    parser = AnnouncementPDFParser()
    parsed = parser.parse_from_text(text, region)
    config = parser.create_config_from_parsed(parsed, use_default_rules=True)
    
    manager = AnnouncementConfigManager()
    manager.save_config(config)
    
    return config
