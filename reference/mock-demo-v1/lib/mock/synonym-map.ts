export const synonymGroups = [
  ["수령", "배송", "재고", "출고", "입고", "대기", "품절"],
  ["장기", "오래", "지속", "수명", "내구", "지원"],
  ["성능", "속도", "칩", "프로세서", "a19", "a18"],
  ["배터리", "사용시간", "충전", "지속시간"],
  ["스피커", "사운드", "음질", "소리"],
  ["무게", "가벼운", "가벼워", "휴대", "그립"],
  ["가격", "예산", "비용", "부담", "고가"],
  ["색상", "컬러", "오렌지", "블랙", "화이트"],
  ["후기", "리뷰", "사용기"],
  ["고장", "파손", "교체", "수리"]
];

export function expandKeywords(words: string[]) {
  const result = new Set(words.map((word) => word.toLowerCase()));
  synonymGroups.forEach((group) => {
    if (group.some((word) => result.has(word))) group.forEach((word) => result.add(word));
  });
  return [...result];
}
