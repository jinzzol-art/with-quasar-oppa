"""
공공임대 기존주택 매입심사 검증 결과 포매터 v3.1

수정사항:
- 검토일자(오늘) vs 발급일자/작성일자(서류기재) 분리 출력
- 인감 일치율 기준 45% 표시
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Optional

from core.data_models import (
    PublicHousingReviewResult,
    DocumentStatus,
    ApplicantType,
    AgentType,
    DocumentDateInfo,
)
from core.verification_rules import RULES_LIST


class ResultFormatter:
    """검증 결과 포매터"""
    
    # 인감 일치율 기준
    SEAL_THRESHOLD = 45.0
    
    @staticmethod
    def to_console(result: PublicHousingReviewResult) -> str:
        """콘솔 출력용 포맷"""
        lines = []
        
        # 헤더
        lines.append("=" * 70)
        lines.append("공공임대 기존주택 매입심사 검증 결과")
        lines.append("=" * 70)
        lines.append("")
        
        # 검토 메타 정보
        lines.append("[ 검토 정보 ]")
        lines.append(f"📅 검토일자: {result.review_date} (오늘)")
        lines.append(f"📍 물건 소재지: {result.property_address or '미확인'}")
        
        # ★ 법인/개인 여부 표시
        is_corp = result.corporate_documents.is_corporation
        owner_name = result.housing_sale_application.owner_info.name or "미확인"
        if is_corp:
            lines.append(f"🏢 소유자 유형: 법인 ★ (개인정보 검증 제외)")
            lines.append(f"   소유자명: {owner_name}")
        else:
            lines.append(f"👤 소유자 유형: 개인")
            lines.append(f"   소유자명: {owner_name}")
        
        if result.applicant_type != ApplicantType.INDIVIDUAL:
            display = result.applicant_type_display
            if not display and result.housing_sale_application.exists:
                display = result.housing_sale_application.owner_info.name
            if display:
                lines.append(f"👤 신청자 유형: {result.applicant_type.value} (인식된 기재: {display})")
            else:
                lines.append(f"👤 신청자 유형: {result.applicant_type.value}")
        else:
            lines.append(f"👤 신청자 유형: {result.applicant_type.value}")
        lines.append(f"🤝 대리인 유형: {result.agent_type.value}")
        lines.append(f"📋 적용 공고일: {result.announcement_date or '미설정'}")
        if result.correction_announcement_date:
            lines.append(f"📋 정정공고일: {result.correction_announcement_date}")
        lines.append("")
        
        # 서류별 발급일/작성일 목록
        lines.append("-" * 70)
        lines.append("[ 서류별 발급일/작성일 ]")
        lines.append("-" * 70)
        
        if result.document_dates:
            for doc_date in result.document_dates:
                status_icon = "✅" if doc_date.is_valid else "❌"
                date_str = doc_date.date_value if doc_date.date_value else "미확인"
                lines.append(f"  {status_icon} {doc_date.document_name} ({doc_date.date_type}): {date_str}")
        else:
            # document_dates가 없으면 개별 서류에서 추출
            lines.append(ResultFormatter._extract_document_dates(result))
        
        lines.append("")
        
        # 모든 내용을 34개 검증 항목 하나에 통합
        status_icon = "✅" if result.is_review_complete else "⚠️"
        status_text = "심사가능" if result.is_review_complete else f"보완필요({result.supplementary_count}건)"
        lines.append(f"[ 34개 검증 항목별 결과 ] {status_icon} {status_text}")
        lines.append("")
        
        # 규칙별 보완서류 매핑
        by_rule: dict[int, list[tuple[str, str]]] = {}
        for doc in result.supplementary_documents or []:
            n = doc.rule_number
            if n not in by_rule:
                by_rule[n] = []
            by_rule[n].append((doc.document_name, doc.reason))
        
        passed_count = 0
        failed_count = 0
        
        agent = result.housing_sale_application.agent_info
        is_proxy = agent.exists and bool(agent.name and str(agent.name).strip())
        is_corp = result.corporate_documents.is_corporation
        is_realtor = getattr(result.realtor_documents, 'is_realtor_agent', False)
        
        for rule_num, rule_name, rule_desc in RULES_LIST:
            if rule_num in by_rule:
                items = by_rule[rule_num]
                reasons = "; ".join(r for (_, r) in items[:2])
                if len(items) > 2:
                    reasons += f" 외 {len(items) - 2}건"
                lines.append(f"{rule_num:2d}. ❌ {rule_desc}")
                lines.append(f"    → {reasons}")
                failed_count += 1
            else:
                if rule_num == 5 and not is_proxy:
                    lines.append(f"{rule_num:2d}. ➖ {rule_desc} (대리접수 아님)")
                elif rule_num in (9, 10, 11) and not is_proxy:
                    lines.append(f"{rule_num:2d}. ➖ {rule_desc} (대리접수 아님)")
                elif rule_num == 15 and not is_corp:
                    lines.append(f"{rule_num:2d}. ➖ {rule_desc} (법인 아님)")
                elif rule_num == 17 and not is_corp:
                    lines.append(f"{rule_num:2d}. ➖ {rule_desc} (법인 아님)")
                elif rule_num == 18 and not is_realtor:
                    lines.append(f"{rule_num:2d}. ➖ {rule_desc} (중개사 아님)")
                else:
                    lines.append(f"{rule_num:2d}. ✅ {rule_desc}")
                passed_count += 1
        
        lines.append("")
        lines.append(f"═ 통과: {passed_count}개 | 보완: {failed_count}개 ═")
        
        # 주요 확인 사항
        lines.append("-" * 70)
        lines.append("📌 주요 확인 사항")
        lines.append("-" * 70)
        
        # 건축물대장 표제부 정보
        bld = result.building_ledger_title
        if bld.exists:
            lines.append("")
            lines.append("[건축물대장 표제부]")
            lines.append(f"  - 사용승인일: {bld.approval_date or '미확인'}")
            
            # 내진설계 표시 개선
            if bld.seismic_design is True:
                lines.append(f"  - 내진설계: 적용 ✅")
            elif bld.seismic_design is False:
                lines.append(f"  - 내진설계: 미적용")
            else:
                lines.append(f"  - 내진설계: 미확인")
            
            lines.append(f"  - 옥외주차장: {bld.outdoor_parking if bld.outdoor_parking is not None else '미확인'}대")
            lines.append(f"  - 옥내주차장: {bld.indoor_parking if bld.indoor_parking is not None else '미확인'}대")
            lines.append(f"  - 기계식주차장: {bld.mechanical_parking if bld.mechanical_parking is not None else '미확인'}대")
            
            # 지하층 표시 개선
            if bld.has_basement is True:
                basement_str = f"있음 ({bld.basement_floors}층)" if bld.basement_floors else "있음"
                lines.append(f"  - 지하층: {basement_str}")
            elif bld.has_basement is False:
                lines.append(f"  - 지하층: 없음")
            else:
                lines.append(f"  - 지하층: 미확인")
            
            # 승강기 표시 개선
            if bld.has_elevator is True:
                elevator_str = f"있음 ({bld.elevator_count}대)" if bld.elevator_count else "있음"
                lines.append(f"  - 승강기: {elevator_str}")
            elif bld.has_elevator is False:
                lines.append(f"  - 승강기: 없음")
            else:
                lines.append(f"  - 승강기: 미확인")
        
        # 준공도면 추출 자재 (도면에서 실제 추출된 자재명 표시)
        ab = result.as_built_drawing
        if ab.exists:
            lines.append("")
            lines.append("[준공도면 추출 자재]")
            lines.append(f"  - 외벽 마감재료: {ab.exterior_finish_material or '미추출'}")
            lines.append(f"  - 외벽 단열재료: {ab.exterior_insulation_material or '미추출'}")
            lines.append(f"  - 필로티 마감재료: {ab.piloti_finish_material or '미추출(해당 없음 포함)'}")
            lines.append(f"  - 필로티 단열재료: {ab.piloti_insulation_material or '미추출(해당 없음 포함)'}")
        
        # 시험성적서 검증 정보 (규칙 30) - 상세 표시
        tcd = result.test_certificate_delivery
        as_built = result.as_built_drawing
        lines.append("")
        lines.append("[준불연시험성적서·납품확인서 검증 (규칙 30)]")
        
        # 파일 제출 여부
        lines.append(f"  📄 시험성적서 파일 제출: {'✅ 제출됨' if tcd.test_cert_file_exists else '❌ 미제출'}")
        lines.append(f"  📄 납품확인서 파일 제출: {'✅ 제출됨' if tcd.delivery_conf_file_exists else '❌ 미제출'}")
        
        # 준공도면에서 추출된 자재
        lines.append("")
        lines.append("  [준공도면 추출 자재 - 검증 대상]")
        ext_f = as_built.exterior_finish_material if as_built.exists else None
        ext_i = as_built.exterior_insulation_material if as_built.exists else None
        pil_f = as_built.piloti_finish_material if as_built.exists else None
        pil_i = as_built.piloti_insulation_material if as_built.exists else None
        
        if ext_f:
            lines.append(f"    • 외벽마감재료: {ext_f}")
        else:
            lines.append(f"    • 외벽마감재료: 미추출 ⚠️")
        if ext_i:
            lines.append(f"    • 외벽단열재료: {ext_i}")
        else:
            lines.append(f"    • 외벽단열재료: 미추출 ⚠️")
        if pil_f:
            lines.append(f"    • 필로티마감재료: {pil_f}")
        elif pil_i:
            lines.append(f"    • 필로티마감재료: 미추출 (필로티 구조)")
        else:
            lines.append(f"    • 필로티마감재료: 해당없음 또는 미추출")
        if pil_i:
            lines.append(f"    • 필로티단열재료: {pil_i}")
        elif pil_f:
            lines.append(f"    • 필로티단열재료: 미추출 (필로티 구조)")
        else:
            lines.append(f"    • 필로티단열재료: 해당없음 또는 미추출")
        
        # ★★★ 시험 항목 검증 - 열방출+가스유해성 조합 필수 ★★★
        lines.append("")
        lines.append("  [시험 항목 검증 - 열방출+가스유해성 조합 필수]")
        
        has_heat = tcd.has_heat_release_test
        has_gas = tcd.has_gas_toxicity_test
        has_thermal = getattr(tcd, "has_thermal_conductivity_test", False)
        has_valid_combo = has_heat and has_gas
        
        if has_heat:
            lines.append(f"    ✅ 열방출시험: 포함됨")
        else:
            lines.append(f"    ❌ 열방출시험: 미포함")
        
        if has_gas:
            lines.append(f"    ✅ 가스유해성 시험: 포함됨")
        else:
            lines.append(f"    ❌ 가스유해성 시험: 미포함")
        
        if has_thermal:
            lines.append(f"    ⚠️ 열전도율 시험: 포함됨 (★이 시험은 인정 안 됨)")
        
        # 최종 판정
        lines.append("")
        if has_valid_combo:
            lines.append(f"    ★ 시험성적서 판정: ✅ 유효 (열방출+가스유해성 조합 충족)")
        elif tcd.test_cert_file_exists:
            if not has_heat and not has_gas:
                if has_thermal:
                    lines.append(f"    ★ 시험성적서 판정: ❌ 무효 (열전도율만 있음, 열방출+가스유해성 필요)")
                else:
                    lines.append(f"    ★ 시험성적서 판정: ❌ 무효 (열방출+가스유해성 둘 다 없음)")
            elif not has_heat:
                lines.append(f"    ★ 시험성적서 판정: ❌ 무효 (열방출시험 없음)")
            else:
                lines.append(f"    ★ 시험성적서 판정: ❌ 무효 (가스유해성 시험 없음)")
        else:
            lines.append(f"    ★ 시험성적서 판정: ❌ 미제출")
        
        # 석재 예외
        if tcd.stone_exterior_exception:
            lines.append(f"    ℹ️  외벽 마감재가 석재로 확인됨 (시험성적서 생략 가능, 납품확인서는 필요)")
        
        # 감지된 시험 항목 목록
        detected = getattr(tcd, "detected_tests", [])
        if detected:
            lines.append(f"    📋 감지된 시험 항목: {', '.join(detected)}")
        
        # 시험성적서/납품확인서가 확인된 자재 목록
        if tcd.materials_with_test_cert:
            lines.append(f"    📋 시험성적서 확인된 자재: {', '.join(tcd.materials_with_test_cert)}")
        if tcd.materials_with_delivery_conf:
            lines.append(f"    📋 납품확인서 확인된 자재: {', '.join(tcd.materials_with_delivery_conf)}")
        
        # 인감 검증 정보
        seal = result.housing_sale_application.seal_verification
        if seal.match_rate is not None:
            lines.append("")
            lines.append("[인감 검증]")
            lines.append(f"  - 일치율: {seal.match_rate:.1f}%")
            lines.append(f"  - 기준: {ResultFormatter.SEAL_THRESHOLD}% 이상")
            if seal.match_rate >= ResultFormatter.SEAL_THRESHOLD:
                lines.append(f"  - 판정: 일치 ✅")
            else:
                lines.append(f"  - 판정: 불일치 ❌")
        
        # 등기부 권리관계
        reg = result.building_registry
        if reg.exists:
            lines.append("")
            lines.append("[건물 등기부등본 권리관계]")
            if reg.has_mortgage:
                lines.append(f"  ⚠️ 근저당 설정: 있음")
                for detail in reg.mortgage_details:
                    lines.append(f"     - {detail}")
            else:
                lines.append(f"  ✅ 근저당 설정: 없음")
            
            if reg.has_seizure:
                lines.append(f"  ⚠️ 압류: 있음")
                for detail in reg.seizure_details:
                    lines.append(f"     - {detail}")
            else:
                lines.append(f"  ✅ 압류: 없음")
            
            if reg.has_trust:
                lines.append(f"  ⚠️ 신탁: 있음")
                for detail in reg.trust_details:
                    lines.append(f"     - {detail}")
            else:
                lines.append(f"  ✅ 신탁: 없음")
        
        # 토지이용규제 사항
        land_use = result.land_use_plan
        if land_use.exists and land_use.land_use_regulations:
            lines.append("")
            lines.append("[토지이용규제 기본법 시행령 제9조 제4항 해당 사항]")
            for reg_item in land_use.land_use_regulations:
                lines.append(f"  - {reg_item}")
        
        # 전용면적 기준 미충족 세대
        excl = result.building_ledger_exclusive
        if excl.invalid_area_units:
            lines.append("")
            lines.append("[전용면적 기준 미충족 세대 (16~85㎡ 범위 외)]")
            for unit in excl.invalid_area_units:
                lines.append(f"  - {unit}호")
        
        lines.append("")
        lines.append("=" * 70)
        lines.append(f"검토 완료: {result.review_summary}")
        lines.append("=" * 70)
        
        return "\n".join(lines)
    
    @staticmethod
    def _extract_document_dates(result: PublicHousingReviewResult) -> str:
        """개별 서류에서 날짜 정보 추출"""
        lines = []
        
        # 주택매도 신청서
        app = result.housing_sale_application
        if app.exists:
            date_val = app.written_date or app.issue_date
            status = "✅" if app.is_after_announcement else "❌"
            lines.append(f"  {status} 주택매도 신청서 (작성일): {date_val or '미확인'}")
        
        # 위임장
        poa = result.power_of_attorney
        if poa.exists:
            date_val = poa.written_date or poa.issue_date
            status = "✅" if poa.is_after_announcement else "❌"
            lines.append(f"  {status} 위임장 (작성일): {date_val or '미확인'}")
        
        # 인감증명서
        if result.owner_identity.seal_certificate.exists:
            date_val = result.owner_identity.seal_certificate_issue_date or result.owner_identity.seal_certificate.issue_date
            lines.append(f"  - 인감증명서 (발급일): {date_val or '미확인'}")
        
        # 토지대장
        land = result.land_ledger
        if land.exists:
            status = "✅" if land.is_after_announcement else "❌"
            lines.append(f"  {status} 토지대장 (발급일): {land.issue_date or '미확인'}")
        
        # 건축물대장
        if result.building_ledger_title.exists:
            lines.append(f"  - 건축물대장 표제부 (발급일): {result.building_ledger_title.issue_date or '미확인'}")
        
        # 개인정보 동의서
        consent = result.consent_form
        if consent.exists:
            lines.append(f"  - 개인정보 동의서 소유자 (작성일): {consent.owner_written_date or '미확인'}")
            if consent.agent_written_date:
                lines.append(f"  - 개인정보 동의서 대리인 (작성일): {consent.agent_written_date}")
        
        # 청렴서약서
        pledge = result.integrity_pledge
        if pledge.exists:
            lines.append(f"  - 청렴서약서 (작성일): {pledge.owner_written_date or '미확인'}")
        
        # 공사직원확인서
        lh = result.lh_employee_confirmation
        if lh.exists:
            lines.append(f"  - 공사직원여부 확인서 (작성일): {lh.written_date or '미확인'}")
        
        return "\n".join(lines) if lines else "  (서류별 날짜 정보 없음)"
    
    @staticmethod
    def to_json(result: PublicHousingReviewResult, indent: int = 2) -> str:
        return result.model_dump_json(indent=indent, ensure_ascii=False)
    
    @staticmethod
    def to_supplementary_list(result: PublicHousingReviewResult) -> str:
        if not result.supplementary_documents:
            return "보완서류 없음 - 모든 서류 정상"
        
        lines = ["[보완서류 목록]", ""]
        
        by_rule: dict[int, list] = {}
        for doc in result.supplementary_documents:
            if doc.rule_number not in by_rule:
                by_rule[doc.rule_number] = []
            by_rule[doc.rule_number].append(doc)
        
        for rule_num in sorted(by_rule.keys()):
            docs = by_rule[rule_num]
            lines.append(f"[규칙 {rule_num}]")
            for doc in docs:
                parts = [p.strip() for p in doc.document_name.split("·") if p.strip()]
                if len(parts) <= 1:
                    lines.append(f"  • {doc.document_name}")
                else:
                    for p in parts:
                        lines.append(f"  • {p}")
                lines.append(f"    → {doc.reason}")
            lines.append("")
        
        lines.append(f"총 {len(result.supplementary_documents)}건의 보완서류 필요")
        
        return "\n".join(lines)


def format_result_for_ui(result: PublicHousingReviewResult) -> str:
    """UI 표시용 포맷"""
    return ResultFormatter.to_console(result)
