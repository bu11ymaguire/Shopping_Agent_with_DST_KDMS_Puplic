export type ParsedInput = {
  category?: string;
  /** 사용자가 말한 예산 상한(원). */
  budget?: number;
  /** 장기 사용 가치가 있으면 상한을 넘겨도 수용한다는 신호. */
  budgetFlexible?: boolean;
  /** 기존 기기 고장 등 교체를 유발한 사건. */
  deviceBroken?: boolean;
  /** 오래 쓸 제품을 원한다는 신호. */
  longevityValue?: boolean;
  /** 리뷰 수를 신뢰 근거로 삼는다는 신호. */
  valuesReviewCount?: boolean;
  /** 발화에서 확인된 수령 기한(일). 확인되면 하드 제약으로 승격한다. */
  deliveryDeadlineDays?: number;
  /** 색상을 고르지 못하고 잔여 옵션을 받아들이는 상황. */
  residualColorChoice?: boolean;
  intent: string[];
  action?:
    | "inspect_current"
    | "reject_first_delivery"
    | "reject_first_audio_battery"
    | "compare_first_second"
    | "purchase_current";
};

const productReference = /(첫\s*(번째|제품)|1번|이\s*제품|이건|이거|이걸|그건|추천(해\s*주신|받은|한)?|iphone|아이폰)/i;

function parseDeadlineDays(text: string) {
  const explicitDays = text.match(/(\d+)\s*일\s*(?:이내|안에|안|내로)/);
  if (explicitDays) return Number(explicitDays[1]);
  if (/이번\s*주\s*(?:안에|내로|중에)?/.test(text)) return 7;
  if (/(지금|당장|현재)\s*(쓸|사용할)?\s*(폰|기기|휴대폰|스마트폰)?\s*이?\s*없/.test(text)) return 3;
  if (/당장|급하게|급해|최대한\s*빨리|바로\s*받아야/.test(text)) return 3;
  return undefined;
}

function parseAction(text: string): ParsedInput["action"] {
  const mentionsProduct = productReference.test(text);
  const stockProblem = /재고|품절|입고|배송|수령|출고/.test(text) && /없|늦|지연|기다|대기|안\s*돼|안돼|오래\s*걸/.test(text);
  const audioBatteryProblem = /스피커|음질|사운드|소리|배터리/.test(text) && /아쉽|약|짧|단점|불만|안\s*좋|별로/.test(text);
  if (mentionsProduct && stockProblem) return "reject_first_delivery";
  if (mentionsProduct && audioBatteryProblem) return "reject_first_audio_battery";
  if (/첫\s*번째.*두\s*번째|1번.*2번/.test(text) && /비교/.test(text)) return "compare_first_second";
  if (mentionsProduct && /살게|구매할|구매하|결제|주문할|이걸로/.test(text)) return "purchase_current";
  if (mentionsProduct && /자세히|상세|살펴|볼게|보고\s*싶/.test(text)) return "inspect_current";
  return undefined;
}

export function parseUserInput(input: string): ParsedInput {
  const normalized = input.replace(/\s+/g, " ").trim();
  const budgetMatch = normalized.match(/(\d+(?:\.\d+)?)\s*만\s*원?\s*(?:이하|미만|정도|까지)?/);
  const hasPhone = /스마트폰|아이폰|iphone|휴대폰|핸드폰|폰/i.test(normalized);
  const deviceBroken = /고장|파손|망가|깨졌|액정|먹통|전원이\s*안/.test(normalized);
  const longevityValue = /오래\s*(쓸|쓰|사용)|장기\s*사용|길게\s*쓸|한동안\s*쓸/.test(normalized);
  const budgetFlexible =
    /(비싸도|더\s*써도|더\s*줘도|넘겨도|넘어도|초과해도|상한을?\s*넘)/.test(normalized) &&
    /괜찮|상관\s*없|가능|좋|의향/.test(normalized);
  const residualColorChoice =
    /색상|컬러/.test(normalized) && /뿐|밖에|남은|고를\s*게\s*없|고르지\s*못|선택할\s*수\s*없/.test(normalized);
  const action = parseAction(normalized);
  const isRejection = action === "reject_first_delivery" || action === "reject_first_audio_battery";
  const valuesReviewCount = !isRejection && /후기|리뷰/.test(normalized) && /많|믿|신뢰|중요/.test(normalized);
  const deliveryDeadlineDays = parseDeadlineDays(normalized);

  const intent = [
    hasPhone ? "상품 탐색" : "대화 갱신",
    deviceBroken ? "교체 사유" : "",
    budgetMatch ? "예산 제약" : "",
    budgetFlexible ? "예산 유연성" : "",
    longevityValue ? "사용 기간 기준" : "",
    deliveryDeadlineDays ? "수령 시점 제약" : "",
    valuesReviewCount ? "리뷰 선호" : "",
    residualColorChoice ? "잔여 옵션 수용" : "",
    action ? "상품 행동" : ""
  ].filter(Boolean);

  return {
    category: hasPhone ? "스마트폰" : undefined,
    budget: budgetMatch ? Math.round(Number(budgetMatch[1]) * 10000) : undefined,
    budgetFlexible,
    deviceBroken,
    longevityValue,
    valuesReviewCount,
    deliveryDeadlineDays,
    residualColorChoice,
    intent,
    action
  };
}
