# Adaptive Coach capability disclosure — legal review draft

Status: **NOT APPROVED FOR PUBLICATION OR ACTIVATION**. F-1 and F-1b are
founder requirements (2026-11-18); the wording below awaits legal review.
This PR changes no disclosure registry, launch version, grant, eligibility,
model routing, quota, client onboarding or runtime permission.

## Required behavior

Ordinary permitted Coach chat remains available to existing r2 users.
Training-plan reading retains its existing permitted Coach scope. Request
additional acceptance only when a user chooses a newly introduced capability:
authorized plan editing, or reading/editing saved Nutrition Plan content.
Declining that update must leave ordinary chat usable and permit retrying the
capability request later. Never infer nutrition permission from a Coach,
Training, Pump Check or unrelated grant.

Training edits require affirmative acceptance of the approved editing
disclosure. Saved Nutrition Plan reads and edits require BOTH the appropriate
`nutrition.plan` permission AND the approved Coach nutrition disclosure scope.
A missing permission withholds plan content from the model, even for reads.

Backend policy decides impact. An explicit low-impact reversible training edit
may apply directly. Destructive, high-impact or ambiguous requests require
confirmation. The approved F-3 fallback requires confirmation for whole-meal
re-estimation when reliable item nutrients are unavailable. Confirm/Cancel
calls the deterministic endpoint and spends no additional Coach turn or chat
allowance; the original request and any estimator calls retain their existing
accounting rules. This is not permission for a universal confirm-all policy.

## Candidate incremental wording — English

### Authorize Coach plan editing

When you ask Coach to change your saved Training Plan, AxisAI can apply the
specific change through its plan services and explain what changed. Explicit,
small, reversible edits may be saved immediately. Changes with greater impact,
destructive effects or unclear intent require your confirmation first. You can
undo supported plan changes. This permission does not authorize generating an
entire new plan through Coach.

### Allow Coach to use your saved Nutrition Plan

If you also grant Nutrition Plan permission, Coach can read your saved meals,
food descriptions and nutrition targets to answer your request. With editing
permission, Coach can prepare specific item replacements, additions or
removals. When reliable item nutrients are unavailable, AxisAI estimates the
entire affected meal and updates the day's totals from its saved meals. These
are estimates; values for other foods in the affected meal may change too.
AxisAI shows the proposed change and asks for confirmation before saving an
edit using this estimation method. If recalculation cannot be completed
reliably, the plan is left unchanged. Supported edits can be undone.

You may decline this additional permission and continue ordinary permitted
Coach chat. You can withdraw the applicable permission in AI permissions.
Withdrawal blocks new actions and pending changes relying on that permission;
it does not itself undo changes already saved.

## Candidate incremental wording — Türkçe

### Koç'un planınızı düzenlemesine izin verin

Koç'tan kayıtlı Antrenman Planınızı değiştirmesini istediğinizde AxisAI,
istediğiniz belirli değişikliği plan hizmetleri üzerinden uygulayabilir ve
neyin değiştiğini açıklayabilir. Açıkça istediğiniz küçük ve geri alınabilir
düzenlemeler hemen kaydedilebilir. Daha büyük etkisi olan, içerik silen veya
niyeti belirsiz değişiklikler önce onayınızı gerektirir. Desteklenen plan
değişikliklerini geri alabilirsiniz. Bu izin, Koç üzerinden tamamen yeni bir
plan oluşturulmasına izin vermez.

### Koç'un kayıtlı Beslenme Planınızı kullanmasına izin verin

Beslenme Planı iznini de verirseniz Koç, isteğinizi yanıtlamak için kayıtlı
öğünlerinizi, besin açıklamalarınızı ve beslenme hedeflerinizi okuyabilir.
Düzenleme izniyle belirli bir besinin değiştirilmesini, eklenmesini veya
kaldırılmasını hazırlayabilir. Besin bazında güvenilir besin değerleri yoksa
AxisAI, etkilenen öğünün tamamını tahmin eder ve günün toplamlarını kayıtlı
öğünlerden yeniden hesaplar. Bu değerler tahminidir; etkilenen öğündeki diğer
besinlerin değerleri de değişebilir. AxisAI, bu tahmin yöntemiyle yapılan
düzenlemeyi kaydetmeden önce önerilen değişikliği gösterir ve onayınızı ister.
Güvenilir bir yeniden hesaplama yapılamazsa plan değiştirilmez. Desteklenen
düzenlemeler geri alınabilir.

Bu ek izni reddedebilir ve izin verilmiş olağan Koç sohbetine devam
edebilirsiniz. İlgili izni Yapay zekâ izinleri bölümünden geri çekebilirsiniz.
Geri çekme, bu izne dayanan yeni işlemleri ve bekleyen değişiklikleri engeller;
önceden kaydedilmiş değişiklikleri kendiliğinden geri almaz.

## Implementation constraints after legal approval

These paragraphs are an extension draft, not a replacement for the current
recipient, processing, retention, transfer, image and withdrawal disclosures.
Counsel must review the complete EN/TR contract together with the existing
texts before immutable versions and digests are registered. Do not publish
the fragments as a complete disclosure or promise unimplemented behavior.

Changing only `LAUNCH_VERSIONS["coach.conversation"]` would deny r2 users at
`ai_launch.require_selected`. Broadening the shared `BINDINGS["coach"]`
categories would also conflict with r2's exact category contract. Both are
unsafe shortcuts. Keep the r2 ordinary-chat path and its existing categories
intact; give new capabilities separately checked immutable scope/version
requirements. Do not weaken `_record_valid`, `_check_operation`, recipient
checks, ownership or generation fencing to accept a historical grant for new
scope. The existing one-grant-per-purpose representation must be accounted
for explicitly before implementing a targeted upgrade.

Acceptance and withdrawal use the existing optimistic generation and replay
semantics. Every provider dispatch and canonical save rechecks the applicable
grants. A prepared proposal retains the original authority and cannot be
revived by revoke/regrant. Confirm/Cancel must not grant permission or invoke
the model to interpret a button.

Required future qualification: r2 chat after upgrade publication/decline;
missing Coach editing scope; missing nutrition grant; old Coach grant plus
nutrition grant; foreign/stale acceptance; revoked or replaced grant during
estimation and confirmation; replay; EN/TR parity; in-flow targeted review
without restarting onboarding. This documentation-only PR does not claim
those checks or the consent implementation have passed.

Release remains held for legal approval, source qualification, separate founder
merge approval and a later explicit deployment/activation decision. App Review
build 101119 is unchanged.
