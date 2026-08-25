"use client";

import Image from "next/image";
import { useState } from "react";
import { Bot, Braces, CheckCircle2, CircleHelp, ClipboardList, RotateCcw, Scale, Send, Star, UserRound } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Switch } from "@/components/ui/switch";
import { cn } from "@/lib/utils";
import productsData from "@/data/products.json";
import reviewsData from "@/data/reviews.json";
import { createInitialDialogueState, updateDialogueState } from "@/lib/rarec/state-manager";
import { rankProducts } from "@/lib/rarec/rank-products";
import { createRecommendationResponse } from "@/lib/rarec/contracts";
import { generateNaturalLanguageQuery } from "@/lib/rarec/generate-query";
import { browseMockCatalog } from "@/lib/spn/browse";
import { composeClarifyResponse, composeRecommendResponse } from "@/lib/spn/compose-response";
import { understandUtterance } from "@/lib/spn/understand";
import { deriveHiddenIntentHypotheses } from "@/lib/spn/derive-intent-hypotheses";
import { selectAction } from "@/lib/spn/select-action";
import type { BrowseResult, ConversationTurn, DialogueState, FinalResponse, PolicyLane, Product, RankedProduct, RecommendationResponse, Review } from "@/lib/types";

const products = productsData as Product[];
const reviews = reviewsData as Review[];
function nextDemoUtterance(state: DialogueState, turn: ConversationTurn | null) {
  if (!turn) return "쓰던 아이폰이 고장 나서 새로 바꿔야 해요.";
  if (turn.policy.action === "ask_user") return "150만 원 정도 생각하는데, 오래 쓸 거면 조금 더 써도 괜찮아요.";
  if (!state.hardConstraints.deliveryDeadline) return "첫 번째 제품은 재고가 없어서 2주 뒤에나 받는대요. 지금 쓸 폰이 없어서 그건 안 돼요.";
  if (!state.softConstraints.audioBatteryPriority) return "이건 스피커랑 배터리가 아쉽다는 후기가 많네요.";
  if (!state.inspectedItems.length) return "그럼 추천해주신 제품을 자세히 볼게요.";
  if (!state.purchasedItems.length) return "색상은 지금 받을 수 있는 게 코스믹 오렌지뿐이네요. 그럼 이걸로 살게요.";
  return null;
}
function price(value: number) { return new Intl.NumberFormat("ko-KR", { style: "currency", currency: "KRW", maximumFractionDigits: 0 }).format(value); }
function productById(id: string) { return products.find((product) => product.id === id); }

/**
 * 노드 구성은 episode/spn-ra-rec-architecture-annotated.html 의 architecture-graph 를 따른다.
 * id / role / owner 는 그 그래프의 값과 동일하다.
 */
type FlowNodeSpec = {
  id: string;
  title: string;
  role: "PLAN" | "MEMORY" | "DECIDE" | "QUERY" | "ACT" | "RANK" | "RESPOND";
  owner: "spn" | "ra-rec";
  artifact: string;
};

const commonNodes: FlowNodeSpec[] = [
  { id: "spn-understanding", title: "SPN Understanding", role: "PLAN", owner: "spn", artifact: "IntentResult · 상태 갱신 후보" },
  { id: "ra-state-manager", title: "RA-Rec State Manager", role: "MEMORY", owner: "ra-rec", artifact: "DialogueState + Diff" },
  { id: "spn-policy", title: "SPN Policy", role: "DECIDE", owner: "spn", artifact: "분기 결정" }
];

const laneNodes: Record<PolicyLane, FlowNodeSpec[]> = {
  "clarify-lane": [
    { id: "spn-response-clarify", title: "SPN Response Composer", role: "RESPOND", owner: "spn", artifact: "추가 질문" }
  ],
  "recommend-lane": [
    { id: "ra-query-generator", title: "RA-Rec Query Generator", role: "QUERY", owner: "ra-rec", artifact: "추천 질의" },
    { id: "spn-browsing-actions", title: "SPN Browsing Actions", role: "ACT", owner: "spn", artifact: "상품 · 리뷰 데이터" },
    { id: "ra-recommendation-engine", title: "RA-Rec Recommendation Engine", role: "RANK", owner: "ra-rec", artifact: "RankedProducts + 근거" },
    { id: "spn-response-recommend", title: "SPN Response Composer", role: "RESPOND", owner: "spn", artifact: "추천 결과" }
  ]
};

function FlowNode({ node, active }: { node: FlowNodeSpec; active: boolean }) {
  const spn = node.owner === "spn";
  return (
    <div className={cn("rounded-xl border px-3 py-2 transition-all", active ? spn ? "border-emerald-400 bg-emerald-50 shadow-sm" : "border-blue-400 bg-blue-50 shadow-sm" : "border-slate-200 bg-white opacity-45")}>
      <div className="flex items-center justify-between gap-2">
        <p className="text-xs font-bold leading-4">{node.title}</p>
        <span className={cn("shrink-0 rounded px-1.5 py-0.5 font-mono text-[9px] font-bold tracking-[0.08em]", active ? spn ? "bg-emerald-600 text-white" : "bg-blue-600 text-white" : "bg-slate-100 text-slate-500")}>{node.role}</span>
      </div>
      <p className={cn("mt-0.5 text-[10px] font-semibold", spn ? "text-emerald-700" : "text-blue-700")}>{node.artifact}</p>
    </div>
  );
}

function FlowArrow() { return <div className="mx-auto h-2.5 w-px bg-slate-300" aria-hidden="true" />; }

function FlowLane({ label, nodes, active }: { label: string; nodes: FlowNodeSpec[]; active: boolean }) {
  return (
    <div className={cn("rounded-xl border p-2.5 transition-all", active ? "border-slate-300 bg-slate-50/80" : "border-dashed border-slate-200 bg-white")}>
      <p className={cn("mb-2 font-mono text-[9px] font-bold tracking-[0.1em]", active ? "text-slate-600" : "text-slate-400")}>{label}</p>
      <div className="space-y-1">
        {nodes.map((node, index) => (
          <div key={node.id}>
            {index > 0 && <FlowArrow />}
            <FlowNode node={node} active={active} />
          </div>
        ))}
      </div>
    </div>
  );
}

function Flow({ turn }: { turn: ConversationTurn | null }) {
  const lane = turn?.policy.lane ?? null;
  const vagueness = turn?.policy.snapshot.vagueness;
  const detail = !turn
    ? "대화를 시작하면 사용자의 구매 맥락과 선호를 파악합니다."
    : turn.policy.action === "ask_user"
      ? turn.policy.questionTarget?.reason ?? "추가 정보가 필요합니다."
      : turn.recommendation?.explanation ?? "리뷰와 제약을 결합해 후보를 계산했습니다.";

  return (
    <section className="self-start overflow-hidden rounded-3xl border border-emerald-200 bg-white/95 shadow-panel">
      <header className="border-b border-emerald-100 bg-emerald-50/60 px-5 py-4">
        <p className="text-xs font-bold text-emerald-700">Live decision trace</p>
        <h2 className="text-lg font-bold">SPN + RA-Rec 파이프라인</h2>
        <p className="mt-1 text-sm text-emerald-900">현재 턴에서 실행된 노드와 그 판단 근거를 보여줍니다.</p>
      </header>
      <div className="space-y-4 p-5">
        <div>
          <div className="rounded-xl border border-slate-300 bg-white px-3 py-2 text-center text-xs font-bold text-slate-700">사용자 발화 · INPUT</div>
          {commonNodes.map((node) => (
            <div key={node.id}>
              <FlowArrow />
              <FlowNode node={node} active={Boolean(turn)} />
            </div>
          ))}
        </div>

        <div className="flex items-center gap-2 text-[10px] font-bold text-slate-500">
          <span className={cn("rounded-full border px-2 py-0.5", lane === "clarify-lane" ? "border-amber-300 bg-amber-50 text-amber-800" : "border-slate-200")}>정보 부족</span>
          <span className="h-px flex-1 bg-slate-200" />
          <span className={cn("rounded-full border px-2 py-0.5", lane === "recommend-lane" ? "border-blue-300 bg-blue-50 text-blue-800" : "border-slate-200")}>정보 충분</span>
        </div>

        <div className="grid gap-2">
          <FlowLane label="CLARIFY PATH" nodes={laneNodes["clarify-lane"]} active={lane === "clarify-lane"} />
          <FlowLane label="RECOMMENDATION PATH" nodes={laneNodes["recommend-lane"]} active={lane === "recommend-lane"} />
        </div>

        <div className="flex items-center gap-2 rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-[10px] font-semibold text-slate-600">
          <RotateCcw className="h-3.5 w-3.5 shrink-0 text-slate-500" />
          <span>에이전트 응답 · OUTPUT → 다음 사용자 발화로 SPN Understanding에 재입력</span>
        </div>

        <Card className={cn("border-2", lane === "clarify-lane" ? "border-amber-300 bg-amber-50/70" : "border-blue-300 bg-blue-50/70")}>
          <CardHeader className="pb-2">
            <CardTitle className="text-base">{lane === "clarify-lane" ? "실행 경로: Clarify Path" : lane === "recommend-lane" ? "실행 경로: Recommendation Path" : "대기 중"}</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3 text-sm leading-6">
            <p className="text-slate-700">{detail}</p>
            {vagueness && (
              <div className="rounded-lg bg-white/80 p-3">
                <p className="text-xs font-bold text-slate-500">모호성 점수</p>
                <p><strong>{vagueness.total}</strong> / 임계값 {vagueness.threshold} → {turn?.policy.action === "ask_user" ? "ASK_USER" : "RECOMMEND"}</p>
              </div>
            )}
            {turn?.intent && (
              <div className="rounded-lg bg-white/80 p-3">
                <p className="text-xs font-bold text-slate-500">IntentResult</p>
                <p className="text-xs">의도 {turn.intent.intents.join(" · ") || "없음"}</p>
                <p className="mt-1 text-xs">상태 갱신 후보 {turn.intent.candidates.length}건{turn.intent.itemAction ? ` · 상품 행동 ${turn.intent.itemAction.name}` : ""}</p>
              </div>
            )}
            {turn?.policy.action === "ask_user" ? (
              <div className="rounded-lg bg-white/80 p-3">
                <p className="text-xs font-bold text-slate-500">선택된 질문</p>
                <p>{turn.finalResponse.message}</p>
              </div>
            ) : turn?.recommendation ? (
              <div className="rounded-lg bg-white/80 p-3">
                <p className="text-xs font-bold text-slate-500">검색 질의</p>
                <p>{turn.query}</p>
              </div>
            ) : (
              <p className="rounded-lg bg-white/80 p-3 text-slate-600">첫 발화를 입력하면 여기에서 실제 판단을 확인할 수 있습니다.</p>
            )}
          </CardContent>
        </Card>
      </div>
    </section>
  );
}

function ProductVisual({ product }: { product: Product }) {
  const [source, setSource] = useState(product.image);
  return <Image src={source} alt={product.title} fill sizes="(max-width: 768px) 100vw, 390px" className="object-cover" onError={() => setSource("/mock-images/phone-white.svg")} />;
}
function ProductCard({ ranking, previous }: { ranking: RankedProduct; previous?: RankedProduct }) {
  const product = productById(ranking.productId);
  if (!product) return null;
  const evidence = reviews.filter((review) => ranking.evidenceReviewIds.includes(review.id));
  const changed = previous && previous.rank !== ranking.rank;
  return <Card className="overflow-hidden border-slate-200 bg-white"><div className="relative h-28"><ProductVisual product={product} /><Badge className="absolute left-3 top-3 bg-white text-blue-700">#{ranking.rank} · {ranking.score.total}점</Badge><Badge className="absolute right-3 top-3 bg-slate-900/80 text-white">mock</Badge></div><CardContent className="space-y-3 p-4"><div className="flex items-start justify-between gap-3"><div><h3 className="text-sm font-bold leading-5">{product.title}</h3><p className="mt-1 text-xs text-slate-500">{product.brand} · {price(product.price)} · 리뷰 {product.reviewCount.toLocaleString("ko-KR")}개</p><p className="mt-0.5 text-xs text-slate-500">수령 {product.metadata.deliveryDays}일 · 색상 {product.metadata.availableColors.length ? product.metadata.availableColors.join(", ") : "잔여 없음"}</p></div><div className="flex items-center gap-1 text-xs font-bold text-amber-700"><Star className="h-3.5 w-3.5 fill-amber-500" />{product.rating}</div></div>{changed && <Badge variant="outline" className="border-emerald-300 text-emerald-700">이전 #{previous.rank} → 현재 #{ranking.rank}</Badge>}<div className="grid grid-cols-2 gap-1 text-[11px] text-slate-600">{Object.entries(ranking.score).filter(([key]) => key !== "total").map(([key, value]) => <div key={key} className="rounded bg-slate-50 px-2 py-1">{key}: {value}</div>)}</div><div className="space-y-1">{evidence.map((review) => <p key={review.id} className="flex gap-1 text-xs leading-5 text-slate-600"><CheckCircle2 className="mt-0.5 h-3.5 w-3.5 shrink-0 text-emerald-600" />{review.text}</p>)}</div></CardContent></Card>;
}

function MemoryPanel({ turn, state }: { turn: ConversationTurn | null; state: DialogueState }) {
  const rankings = turn?.recommendation?.rankedProducts ?? [];
  const previous = new Map((turn?.previousRankings ?? []).map((item) => [item.productId, item]));
  const requirements = [["카테고리", state.category?.valueText], ["교체 사유", state.subjectiveNeeds.event?.valueText], ["가격 기준", state.hardConstraints.budget?.valueText], ["예산 유연성", state.softConstraints.budgetFlexibility?.valueText], ["사용 기간 기준", state.subjectiveNeeds.subjective_property?.valueText], ["수령 시점", state.hardConstraints.deliveryDeadline?.valueText], ["스피커·배터리", state.softConstraints.audioBatteryPriority?.valueText], ["색상", state.softConstraints.colorChoice?.valueText]] as const;
  const stateJson = JSON.stringify(state, null, 2);
  return <aside className="self-start space-y-4"><Card className="border-blue-200"><CardHeader className="pb-2"><p className="text-xs font-bold text-slate-400">Pipeline Inspector · State</p><CardTitle className="text-lg">현재 이해한 사용자 요구</CardTitle></CardHeader><CardContent className="grid gap-2 text-xs">{requirements.map(([label, value]) => <div key={label} className="flex items-center justify-between gap-3 rounded-lg bg-slate-50 px-3 py-2.5"><span className="font-bold text-slate-500">{label}</span><span className={value ? "font-semibold text-slate-800" : "text-slate-400"}>{value ?? "미확인 · 추가 질문 필요"}</span></div>)}</CardContent></Card><Card className="border-emerald-200"><CardHeader className="pb-2"><p className="text-xs font-bold text-emerald-700">Latent intent</p><CardTitle className="flex items-center gap-2 text-lg"><CircleHelp className="h-5 w-5 text-emerald-700" /> 숨은 의도 가설</CardTitle></CardHeader><CardContent className="space-y-2">{turn?.hiddenIntentHypotheses.length ? turn.hiddenIntentHypotheses.map((hypothesis) => <div key={hypothesis.id} className="rounded-lg bg-emerald-50 p-3 text-xs leading-5"><div className="flex items-start justify-between gap-2"><p className="font-semibold text-emerald-950">{hypothesis.text}</p><Badge variant="outline" className="shrink-0 border-emerald-200 bg-white text-emerald-700">{Math.round(hypothesis.confidence * 100)}%</Badge></div><p className="mt-1 text-emerald-700">Origin: inferred · Status: unconfirmed · Influence: limited</p><p className="mt-1 text-emerald-700">Evidence: {hypothesis.evidence.join(" · ")}</p></div>) : <p className="rounded-lg bg-emerald-50 p-3 text-sm leading-6 text-emerald-900">대화를 시작하면 명시된 조건뿐 아니라 구매 동기에 대한 가설을 함께 보여줍니다.</p>}</CardContent></Card><Card className="border-rose-200"><CardHeader className="pb-2"><p className="text-xs font-bold text-rose-700">Rejections &amp; trade-offs</p><CardTitle className="flex items-center gap-2 text-lg"><Scale className="h-5 w-5 text-rose-600" /> 탈락 이유와 우선순위</CardTitle></CardHeader><CardContent className="space-y-2 text-xs">{state.rejectedItems.length ? state.rejectedItems.map((item) => <div key={item.productId} className="rounded-lg bg-rose-50 p-3 leading-5"><p className="font-semibold text-rose-950">{productById(item.productId)?.title ?? item.productId}</p><p className="mt-1 text-rose-700">{item.reason.valueText}</p></div>) : <p className="rounded-lg bg-rose-50 p-3 text-slate-600">아직 탈락한 후보가 없습니다. 거절 이유는 상황적 제약과 상품 속성으로 구분해 기록합니다.</p>}{state.tradeoffs.map((tradeoff) => <div key={tradeoff.id} className="rounded-lg bg-slate-900 p-3 leading-5 text-slate-100"><p className="text-[10px] font-bold uppercase tracking-[0.14em] text-slate-400">Trade-off</p><p className="mt-1">{tradeoff.valueText}</p></div>)}</CardContent></Card><Card className="border-amber-200"><CardHeader className="pb-2"><p className="text-xs font-bold text-amber-700">Policy decision</p><CardTitle className="text-lg">다음 행동과 판단 근거</CardTitle></CardHeader><CardContent className="space-y-2 text-sm">{turn ? <><div className="grid grid-cols-2 gap-1 rounded-lg bg-slate-50 p-2 text-xs"><span>ASK_USER <strong>{turn.policy.action === "ask_user" ? "0.82" : "0.18"}</strong></span><span>RETRIEVE <strong>{turn.policy.action === "recommend" ? "0.86" : "0.41"}</strong></span><span>COMPARE <strong>0.08</strong></span><span>ITEM_QA <strong>0.00</strong></span></div><div className="rounded-lg bg-amber-50 p-3"><strong>다음 행동</strong><p className="mt-1">{turn.policy.action === "ask_user" ? "사용자에게 추가 질문" : "상품과 리뷰를 탐색해 추천 생성"}</p><p className="mt-1 font-mono text-[10px] text-amber-700">lane: {turn.policy.lane}</p></div><div className="rounded-lg bg-amber-50 p-3"><strong>선택 이유</strong><p className="mt-1">{turn.policy.questionTarget?.reason ?? turn.policy.reasons[0]}</p></div></> : <p className="rounded-lg bg-amber-50 p-3 text-slate-600">첫 발화 이후 Policy가 정보 이득이 높은 행동을 선택합니다.</p>}</CardContent></Card><Card className="border-blue-200"><CardHeader className="pb-2"><p className="text-xs font-bold text-blue-700">State update</p><CardTitle className="flex items-center gap-2 text-lg"><ClipboardList className="h-5 w-5 text-blue-600" /> 이번 턴 State Diff</CardTitle></CardHeader><CardContent>{turn?.diff.changedPaths.length ? <div className="space-y-1 rounded-lg bg-slate-950 p-3 font-mono text-xs leading-5 text-slate-100"><p className="mb-2 text-slate-400">Evidence: {turn.user}</p>{turn.diff.changedPaths.map((path) => <p key={path}><span className="mr-2 text-emerald-400">+</span>{path}</p>)}</div> : <p className="rounded-lg bg-slate-50 p-3 text-sm text-slate-500">아직 입력이 없습니다.</p>}</CardContent></Card><Card className="border-indigo-200"><CardHeader className="pb-2"><p className="text-xs font-bold text-indigo-700">RA-Rec state store</p><CardTitle className="flex items-center gap-2 text-lg"><Braces className="h-5 w-5 text-indigo-600" /> DialogueState JSON</CardTitle></CardHeader><CardContent><details className="rounded-lg border border-indigo-100 bg-indigo-50/50 p-3"><summary className="cursor-pointer text-sm font-semibold text-indigo-950">원시 상태 JSON 보기</summary><p className="mt-2 text-xs leading-5 text-indigo-800"><code>valueText</code>에는 자연어 값을, 나머지 필드에는 출처·신뢰도·상태·turn 근거를 함께 저장합니다.</p><pre className="mt-3 max-h-80 overflow-auto rounded-lg bg-slate-950 p-3 font-mono text-[11px] leading-5 text-slate-100 scrollbar-thin"><code>{stateJson}</code></pre></details></CardContent></Card>{rankings.length ? <><Card className="border-blue-200"><CardHeader className="pb-2"><CardTitle className="text-sm">Top-k Review Evidence</CardTitle></CardHeader><CardContent className="space-y-2">{turn?.recommendation?.reviewEvidence.slice(0, 3).map((review) => <div key={review.id} className="rounded-xl bg-slate-50 p-2 text-xs"><strong>{review.totalScore}점</strong> · {review.text}</div>)}</CardContent></Card>{rankings.map((ranking) => <ProductCard key={ranking.productId} ranking={ranking} previous={previous.get(ranking.productId)} />)}</> : null}</aside>;
}

export default function Home() {
  const [state, setState] = useState<DialogueState>(() => createInitialDialogueState());
  const [turns, setTurns] = useState<ConversationTurn[]>([]);
  const [showArchitecture, setShowArchitecture] = useState(true);
  const currentTurn = turns.at(-1) ?? null;
  const nextDemoInput = nextDemoUtterance(state, currentTurn);

  const processInput = (raw: string) => {
    const text = raw.trim();
    if (!text) return;
    const turnId = `turn-${turns.length + 1}`;
    // "첫 번째 제품" 같은 지시 표현을 해석하기 위한 직전 턴의 순위.
    const previousRankings = rankProducts(state, products, reviews).rankedProducts;

    const intent = understandUtterance(text, state);                                     // SPN Understanding · PLAN
    const updated = updateDialogueState(state, intent, previousRankings, turnId, products); // RA-Rec State Manager · MEMORY
    const policy = selectAction(updated.state);                                          // SPN Policy · DECIDE
    const stateAfterPolicy = { ...updated.state, unresolvedPreferences: policy.questionTarget ? [policy.questionTarget] : [] };

    let query: string | null = null;
    let browseResult: BrowseResult | null = null;
    let recommendation: RecommendationResponse | null = null;
    let nextState = stateAfterPolicy;
    let finalResponse: FinalResponse;

    if (policy.lane === "recommend-lane") {
      query = generateNaturalLanguageQuery(stateAfterPolicy);                                                        // RA-Rec Query Generator · QUERY
      browseResult = browseMockCatalog(query, stateAfterPolicy, products, reviews);                                   // SPN Browsing Actions · ACT
      recommendation = createRecommendationResponse(stateAfterPolicy, browseResult.products, browseResult.reviews);   // RA-Rec Recommendation Engine · RANK
      nextState = { ...stateAfterPolicy, recommendedItems: recommendation.rankedProducts.map((ranking) => ranking.productId) };
      finalResponse = composeRecommendResponse({ ...recommendation, updatedDialogueState: nextState }, browseResult, products, intent.itemAction); // SPN Response Composer · RESPOND
    } else {
      finalResponse = composeClarifyResponse(policy);                                                                // SPN Response Composer · RESPOND
    }

    const hiddenIntentHypotheses = deriveHiddenIntentHypotheses(nextState);
    const turn: ConversationTurn = { id: turnId, user: text, assistant: finalResponse.message, intent, state: nextState, diff: updated.diff, policy, query, browseResult, recommendation, finalResponse, previousRankings, hiddenIntentHypotheses };
    setState(nextState); setTurns((items) => [...items, turn]);
  };

  return <main className="mx-auto min-h-screen w-full max-w-[1780px] p-4 text-slate-900 md:p-6"><header className="mb-4 flex flex-wrap items-center justify-between gap-3 rounded-3xl border border-white/70 bg-white/75 px-5 py-4 shadow-panel backdrop-blur"><div><p className="text-xs font-bold uppercase tracking-[0.18em] text-emerald-700">State-based mock shopping architecture</p><h1 className="mt-1 text-xl font-bold md:text-2xl">SPN Orchestrator / RA-Rec</h1><p className="mt-1 text-sm text-slate-500">발화가 상태·리뷰 근거·점수·순위를 실제로 바꾸는 결정론적 로컬 mock 데모입니다.</p></div><div className="flex items-center gap-3"><Badge variant="outline" className="border-emerald-200 bg-emerald-50 text-emerald-700">SPN: Orchestrator</Badge><Badge variant="outline" className="border-blue-200 bg-blue-50 text-blue-700">RA-Rec: State + Ranking</Badge><Switch checked={showArchitecture} onCheckedChange={setShowArchitecture} aria-label="아키텍처 보기" /></div></header><div className={cn("grid gap-4", showArchitecture ? "xl:grid-cols-[minmax(340px,0.85fr)_minmax(420px,1fr)_minmax(390px,0.95fr)]" : "mx-auto max-w-4xl")}>
    <section className="grid h-[720px] max-h-[calc(100vh-2rem)] min-w-0 self-start grid-rows-[auto_1fr_auto] overflow-hidden rounded-3xl border border-slate-200 bg-white/95 shadow-panel xl:sticky xl:top-4"><header className="flex min-w-0 items-center justify-between gap-3 border-b border-slate-100 px-5 py-4"><div className="min-w-0"><p className="text-xs font-bold text-slate-400">User conversation</p><h2 className="truncate text-lg font-bold">스마트폰 교체 상담</h2></div><Button className="shrink-0" variant="outline" size="sm" onClick={() => { setState(createInitialDialogueState()); setTurns([]); }}>데모 재설정</Button></header><div className="min-w-0 space-y-5 overflow-auto p-5 scrollbar-thin">{turns.length ? turns.map((turn) => <div key={turn.id} className="space-y-3"><div className="flex justify-end gap-3"><div className="min-w-0 max-w-[86%] break-words rounded-2xl rounded-br-md bg-blue-600 px-4 py-3 text-sm leading-6 text-white">{turn.user}</div><UserRound className="mt-2 h-5 w-5 text-slate-400" /></div><div className="flex gap-3"><Bot className="mt-2 h-5 w-5 text-blue-600" /><div className="min-w-0 max-w-[86%] break-words rounded-2xl rounded-bl-md border border-slate-200 px-4 py-3 text-sm leading-6">{turn.assistant}</div></div></div>) : <div className="grid h-full w-full min-w-0 place-items-center px-2 text-center text-sm leading-6 text-slate-500 break-words">대화를 시작하면 에이전트가 구매 목적과 선호를 파악합니다.<br />제품을 선택하거나 거절한 행동도 다음 추천에 반영됩니다.</div>}</div><footer className="border-t border-slate-100 p-4"><div className="flex min-h-16 items-center justify-between gap-3 rounded-2xl border border-slate-200 bg-slate-50 px-4 py-2"><div className="min-w-0"><p className="text-sm font-semibold text-slate-700">다음 대화 진행</p><p className="mt-0.5 text-xs text-slate-500">전송 버튼을 누르면 현재 단계에 맞는 발화가 이어집니다.</p></div><Button type="button" size="icon" className="h-11 w-11 shrink-0 rounded-full" disabled={!nextDemoInput} onClick={() => { if (nextDemoInput) processInput(nextDemoInput); }} aria-label="다음 데모 대화 전송"><Send className="h-5 w-5" /></Button></div><p className="mt-2 text-center text-xs text-slate-400">모든 상품·리뷰·점수는 설명용 local mock 데이터입니다.</p></footer></section>{showArchitecture && <Flow turn={currentTurn} />}{showArchitecture && <MemoryPanel turn={currentTurn} state={state} />}</div></main>;
}
