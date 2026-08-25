const turns = [
  {
    user: "태블릿을 처음 사보는데, 뭘 보고 골라야 할지 잘 모르겠어요.",
    agent: "처음 구매하시는 상황이군요. 지금은 아직 선호가 뚜렷하지 않으니 먼저 사용 목적, 예산, 화면 크기, 브랜드 선호를 차례로 좁혀보면 좋아요. 영상 보기, 필기, 웹서핑, 업무 중에서 가장 가능성이 높은 용도가 있을까요?",
    title: "기준을 파악하는 중이에요",
    summary: "사용자는 태블릿 구매 경험이 부족해서 선택 기준 자체가 아직 없습니다. 추천보다 먼저 의도와 기본 제약을 수집해야 하는 단계입니다.",
    label: "태블릿 구매 경험 부족으로 인한 선택 기준 부재",
    tags: ["초보 구매자", "태블릿 추천 요청", "선호 미정"],
    state: {
      intent: "Provide Preference + Ask For Recommendation",
      constraints: "구매 경험 부족, 용도 미정",
      action: "Request Information",
      currentItem: "없음"
    },
    pipeline: [
      ["Intent Classification", "사용자가 명시적인 제품 조건을 말하기보다 추천 도움을 요청한 것으로 분류합니다.", "Provide Preference + Ask For Recommendation"],
      ["State Update", "초보 구매자이며 아직 용도와 예산이 비어 있다는 상태를 저장합니다.", "초보 구매자, 태블릿 추천 요청"],
      ["Action Classification", "후보 추천을 바로 하기보다 선택 기준을 묻는 것이 우선이라고 판단합니다.", "Request Information"],
      ["Response Generation", "사용 목적을 묻는 질문으로 다음 턴의 제약 추출을 유도합니다.", "용도 질문 생성"]
    ]
  },
  {
    user: "아직 잘 모르겠어요.",
    agent: "괜찮아요. 목적이 아직 정해지지 않았다면 처음 구매자에게 중요한 기준부터 잡아볼게요. 부담 없이 시작하려면 가격대와 후기 신뢰도를 먼저 보는 방식이 좋습니다. 예산은 대략 10만 원대, 20만 원대, 30만 원 이하 중 어디가 편하신가요?",
    title: "불확실성을 줄이기 위해 예산을 물어요",
    summary: "사용 목적이 비어 있으므로 시스템은 더 쉬운 제약인 가격대부터 확인합니다. 이는 추천 후보를 과도하게 넓히지 않기 위한 정보 요청입니다.",
    label: "사용 목적 미정, 예산 확인 필요",
    tags: ["용도 미정", "추천 기준 미정", "예산 질문"],
    state: {
      intent: "Provide Preference",
      constraints: "사용 목적 미정",
      action: "Request Information",
      currentItem: "없음"
    },
    pipeline: [
      ["Intent Classification", "사용자가 아직 구체 조건을 확정하지 못했다고 응답한 것으로 해석합니다.", "Provide Preference"],
      ["State Update", "용도 미정과 추천 기준 미정을 상태에 남깁니다.", "사용 목적 미정, 추천 기준 미정"],
      ["Action Classification", "추천 정확도를 높이기 위해 예산 정보를 추가로 요청합니다.", "Request Information"],
      ["Response Generation", "초보자가 답하기 쉬운 가격대 질문으로 대화를 이어갑니다.", "예산 질문 생성"]
    ]
  },
  {
    user: "너무 비싸진 않았으면 좋겠고, 그래도 후기가 많은 게 좋을 것 같아요.",
    agent: "예산은 부담을 낮추고, 리뷰 수가 많은 제품을 더 신뢰하시는군요. 이 조건을 기준으로 후보를 먼저 필터링해볼게요. 현재 조건은 입문용, 가성비, 리뷰 수 중요, 고가 제품 제외로 정리됩니다.",
    products: [
      ["안드로이드 태블릿 10인치, 4GB+64GB", "131,000원", "리뷰 수가 많고 가격이 낮아 입문용으로 부담이 적습니다."],
      ["LECTRUS 태블릿 10인치 옥타코어 32GB", "135,000원", "기본 성능 중심의 저가형 후보입니다."],
      ["ALLDOCUBE iPlay 50 10.4인치", "175,500원", "RAM이 더 여유 있어 사용 안정성을 기대할 수 있습니다."]
    ],
    title: "가격 부담과 리뷰 신뢰도를 저장했어요",
    summary: "처음으로 추천에 쓸 수 있는 명확한 제약이 들어왔습니다. 시스템은 가격 부담을 낮추고 리뷰 수가 많은 후보를 우선하도록 상태를 갱신합니다.",
    label: "낮은 가격과 많은 리뷰 선호",
    tags: ["입문용", "가성비", "리뷰 수 중요", "고가 제품 제외"],
    state: {
      intent: "Provide Preference",
      constraints: "예산 부담 낮음, 리뷰 많은 제품 선호",
      action: "Recommend",
      currentItem: "후보 3개 생성"
    },
    pipeline: [
      ["Intent Classification", "가격과 리뷰 수에 대한 선호를 제공한 발화로 분류합니다.", "Provide Preference"],
      ["State Update", "가성비, 리뷰 수 중요, 고가 제품 제외 조건을 누적 상태에 저장합니다.", "hard/soft constraints 갱신"],
      ["Action Classification", "필수 정보가 일부 확보되어 조건 기반 추천을 수행합니다.", "Recommend"],
      ["Response Generation", "필터링된 후보를 가격과 리뷰 신뢰도 중심으로 설명합니다.", "조건 기반 추천 + 메타데이터 근거"]
    ]
  },
  {
    user: "사용 후기가 궁금해요.",
    agent: "지금 후보 중에서는 리뷰가 많은 제품을 우선해서 비교해드릴게요. 안드로이드 태블릿 10인치, 4GB+64GB는 리뷰 수가 많아 실제 사용자 반응을 보기 좋고, 평점도 무난한 편입니다. 다만 입문형 제품이라 고사양 게임이나 전문 필기 작업보다는 영상 시청, 검색, 간단한 앱 사용에 더 잘 맞습니다.\n\nALLDOCUBE iPlay 50은 리뷰 수만으로는 가장 압도적인 후보는 아니지만, RAM과 화면 크기 측면에서 사용 안정성을 기대할 수 있습니다. 가격을 조금 더 쓰더라도 오래 쓸 가능성을 보고 싶다면 이쪽이 더 적합합니다.",
    title: "추천이 아니라 질문에 답하는 단계예요",
    summary: "사용자는 새 조건을 말한 것이 아니라 후보에 대한 사용 후기를 물었습니다. 시스템은 현재 후보를 유지하고 리뷰 기반 답변을 생성합니다.",
    label: "후보 제품의 사용 후기 문의",
    tags: ["Inquire", "사용 후기", "리뷰 수", "실사용 안정성"],
    state: {
      intent: "Inquire",
      constraints: "사용 후기, 리뷰 수, 실사용 안정성 관심",
      action: "Answer",
      currentItem: "추천 후보 유지"
    },
    pipeline: [
      ["Intent Classification", "후보 제품에 대한 정보 질문으로 분류합니다.", "Inquire"],
      ["State Update", "현재 관심 기준을 사용 후기, 리뷰 수, 실사용 안정성으로 보강합니다.", "curr_items 유지 + 관심 기준 갱신"],
      ["Action Classification", "새 추천보다 질문 답변이 더 적절하다고 판단합니다.", "Answer"],
      ["Response Generation", "리뷰와 제품 메타데이터를 근거로 후보별 장단점을 설명합니다.", "리뷰 기반 답변 생성"]
    ]
  },
  {
    user: "리뷰 많은 게 믿음이 가네요.",
    agent: "그럼 추천 기준을 리뷰 수 우선, 입문용, 낮은 가격으로 더 확정하겠습니다. 이 기준에서는 안드로이드 태블릿 10인치, 4GB+64GB가 가장 먼저 볼 만합니다. 가격이 낮고 리뷰 수가 많아서 처음 구매자가 실패 위험을 줄이기 좋은 선택지입니다.\n\n다만 오래 쓰는 안정성을 조금 더 보고 싶다면 ALLDOCUBE iPlay 50을 두 번째 후보로 두는 것이 좋습니다. 정리하면, 가격과 리뷰 신뢰도를 가장 중요하게 보면 첫 번째 제품, 조금 더 나은 사용 여유를 원하면 두 번째 제품입니다.",
    title: "리뷰 수 우선으로 후보를 재정렬했어요",
    summary: "사용자의 발화를 장기 선호로 확정합니다. 후보 목록을 단순 반복하지 않고 누적 상태에 따라 우선순위를 다시 계산합니다.",
    label: "리뷰 수 우선 선호 확정",
    tags: ["리뷰 수 우선", "입문용", "낮은 가격", "후보 압축"],
    state: {
      intent: "Provide Preference",
      constraints: "리뷰 수 우선 선호 확정",
      action: "Recommend",
      currentItem: "안드로이드 태블릿 우선"
    },
    pipeline: [
      ["Intent Classification", "리뷰 수를 신뢰 기준으로 삼겠다는 선호 제공으로 분류합니다.", "Provide Preference"],
      ["State Update", "리뷰 수 우선 조건을 확정하고 추천 후보의 우선순위를 조정합니다.", "추천 기준 재정렬"],
      ["Action Classification", "변경된 선호에 따라 다시 추천하는 것이 적절하다고 판단합니다.", "Recommend"],
      ["Response Generation", "누적된 조건을 반영해 1순위와 2순위 후보를 압축해 제시합니다.", "최종 후보 압축"]
    ]
  },
  {
    user: "그럼 첫 번째 제품을 먼저 자세히 볼게요.",
    agent: "좋아요. 안드로이드 태블릿 10인치, 4GB+64GB를 현재 관심 제품으로 저장해둘게요. 다음에는 이 제품의 장단점, 리뷰 요약, 비슷한 가격대 대안 중 원하는 방식으로 이어서 비교해드릴 수 있습니다.",
    title: "관심 제품을 저장했어요",
    summary: "사용자가 추천 후보를 받아들였으므로 시스템은 해당 제품을 현재 관심 제품과 수락 항목에 저장합니다. 이후 질문은 이 제품을 중심으로 이어집니다.",
    label: "첫 번째 추천 제품 수락",
    tags: ["Accept Recommendation", "현재 관심 제품", "accepted_items"],
    state: {
      intent: "Accept Recommendation",
      constraints: "리뷰 많은 저가형 입문 태블릿 선호",
      action: "Post Acceptance Action",
      currentItem: "안드로이드 태블릿 10인치, 4GB+64GB"
    },
    pipeline: [
      ["Intent Classification", "첫 번째 제품을 자세히 보겠다는 수락 의도로 분류합니다.", "Accept Recommendation"],
      ["State Update", "수락한 제품을 accepted_items와 curr_items에 저장합니다.", "accepted_items 갱신"],
      ["Action Classification", "추천 수락 이후의 후속 행동을 선택합니다.", "Post Acceptance Action"],
      ["Response Generation", "현재 관심 제품으로 저장했음을 알리고 다음 탐색 방향을 제안합니다.", "후속 탐색 안내"]
    ]
  }
];

const conversation = document.querySelector("#conversation");
const pipelineList = document.querySelector("#pipelineList");
const insightTitle = document.querySelector("#insightTitle");
const insightSummary = document.querySelector("#insightSummary");
const understandingLabel = document.querySelector("#understandingLabel");
const turnCounter = document.querySelector("#turnCounter");
const stateTags = document.querySelector("#stateTags");
const stateSnapshot = document.querySelector("#stateSnapshot");
const copyButton = document.querySelector("#copyButton");
const finishButton = document.querySelector("#finishButton");

function createMessage(role, text) {
  const row = document.createElement("div");
  row.className = `message-row ${role}`;

  if (role === "agent") {
    const speaker = document.createElement("div");
    speaker.className = "speaker";
    speaker.textContent = "AI";
    row.appendChild(speaker);
  }

  const bubble = document.createElement("div");
  bubble.className = "bubble";
  bubble.textContent = text;
  row.appendChild(bubble);
  return row;
}

function renderConversation() {
  turns.forEach((turn, index) => {
    const wrapper = document.createElement("section");
    wrapper.className = "turn";
    wrapper.dataset.turn = index;
    wrapper.appendChild(createMessage("user", turn.user));

    const card = document.createElement("button");
    card.type = "button";
    card.className = "turn-card";
    card.addEventListener("click", () => selectTurn(index));
    card.appendChild(createMessage("agent", turn.agent));

    if (turn.products) {
      const grid = document.createElement("div");
      grid.className = "product-grid";
      turn.products.forEach(([name, price, description]) => {
        const item = document.createElement("article");
        item.className = "product-card";
        item.innerHTML = `<strong>${name}</strong><span>${price}</span><p>${description}</p>`;
        grid.appendChild(item);
      });
      card.appendChild(grid);
    }

    wrapper.appendChild(card);
    conversation.appendChild(wrapper);
  });
}

function renderPipeline(turn) {
  pipelineList.innerHTML = "";
  turn.pipeline.forEach(([name, description, value], index) => {
    const step = document.createElement("article");
    step.className = "pipeline-step";
    step.innerHTML = `
      <div class="step-index">${index + 1}</div>
      <div class="step-body">
        <strong>${name}</strong>
        <p>${description}</p>
        <div class="value">${value}</div>
      </div>
    `;
    pipelineList.appendChild(step);
  });
}

function renderState(turn) {
  stateTags.innerHTML = "";
  turn.tags.forEach((tag) => {
    const tagNode = document.createElement("span");
    tagNode.textContent = tag;
    stateTags.appendChild(tagNode);
  });

  stateSnapshot.innerHTML = "";
  Object.entries({
    "의도": turn.state.intent,
    "제약": turn.state.constraints,
    "행동": turn.state.action,
    "관심 제품": turn.state.currentItem
  }).forEach(([key, value]) => {
    const row = document.createElement("div");
    row.innerHTML = `<dt>${key}</dt><dd>${value}</dd>`;
    stateSnapshot.appendChild(row);
  });
}

function selectTurn(index) {
  const turn = turns[index];
  document.querySelectorAll(".turn-card").forEach((card, cardIndex) => {
    card.classList.toggle("active", cardIndex === index);
  });

  insightTitle.textContent = turn.title;
  insightSummary.textContent = turn.summary;
  understandingLabel.textContent = turn.label;
  turnCounter.textContent = `(${index + 1}/${turns.length})`;
  renderPipeline(turn);
  renderState(turn);
}

copyButton.addEventListener("click", async () => {
  const text = turns.map((turn) => `나: ${turn.user}\n\n에이전트: ${turn.agent}`).join("\n\n");
  try {
    await navigator.clipboard.writeText(text);
    copyButton.textContent = "복사됨";
    setTimeout(() => { copyButton.textContent = "복사"; }, 1400);
  } catch {
    copyButton.textContent = "복사 실패";
    setTimeout(() => { copyButton.textContent = "복사"; }, 1400);
  }
});

finishButton.addEventListener("click", () => selectTurn(turns.length - 1));

document.querySelectorAll("[data-jump]").forEach((button) => {
  button.addEventListener("click", () => {
    const index = Number(button.dataset.jump);
    selectTurn(index);
    document.querySelector(`[data-turn="${index}"]`)?.scrollIntoView({ behavior: "smooth", block: "center" });
  });
});

renderConversation();
selectTurn(0);
