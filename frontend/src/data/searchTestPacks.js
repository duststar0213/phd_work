/**
 * Local look-up test packs. Not a fixed corpus the search uses at runtime —
 * CanvasBoard embeds whatever notes are live. These packs only give you a
 * fast way to accumulate *different* boards and re-run the same query.
 *
 * Wave 1: product / familiar tools
 * Wave 2: studio teaching (should not steal a "Photoshop" query)
 * Wave 3: climate / health (another unrelated cluster)
 */
export const SEARCH_TEST_PACKS = [
  {
    id: 'tools',
    tryQuery: 'familiar software',
    ideas: [
      {
        text: 'Import papers, screenshots, and sketches instead of recreating them in the tool.',
        rationale: 'People already work in other apps. Forcing in-app creation feels pointless.',
        pins: ['familiar tools', 'import existing artifacts'],
      },
      {
        text: 'Keep using Photoshop / Figma and drop exports onto the board.',
        rationale: 'The workspace should accept what they already made.',
        pins: ['external artifacts'],
      },
      {
        text: 'A sticky note is a thought, not a document editor.',
        rationale: 'Short fragments are easier to move and compare.',
        pins: ['lightweight capture'],
      },
      {
        text: 'Record vague gut feelings next to explicit reasons.',
        rationale: 'Intuition disappears if we only keep tidy labels.',
        pins: ['intuition', 'explicit rationale'],
      },
      {
        text: 'Merge a cited paragraph with my own reaction on one card.',
        rationale: 'Reference and opinion should stay attached.',
        pins: ['reference plus reflection'],
      },
      {
        text: 'Do not rebuild a chat log under every idea.',
        rationale: 'Chat hides the board. The board is the memory.',
        pins: ['no chatbot under notes'],
      },
      {
        text: 'Let people pin three words that name why an idea matters.',
        rationale: 'Pins are the handles later search can grab.',
        pins: ['pinned rationale'],
      },
      {
        text: 'Abandon an idea without deleting the ghost.',
        rationale: 'Dropped paths still explain the repertoire.',
        pins: ['abandoned but visible'],
      },
      {
        text: 'Connect two notes only when the relation itself has a reason.',
        rationale: 'A line without rationale is decoration.',
        pins: ['relation rationale'],
      },
      {
        text: 'Jump to an off-screen note from a look-up list.',
        rationale: 'A window-sized board cannot show a hundred cards at once.',
        pins: ['find then jump'],
      },
    ],
  },
  {
    id: 'studio',
    tryQuery: 'studio critique',
    ideas: [
      {
        text: 'Weekly desk crits where the guest only asks questions.',
        rationale: 'The student should still own the next move.',
        pins: ['critique as questions'],
      },
      {
        text: 'A shared wall of failed experiments, not only polished finals.',
        rationale: 'Process is the curriculum, not the poster.',
        pins: ['process over polish'],
      },
      {
        text: 'Peer review with a two-sentence protocol: notice, then wonder.',
        rationale: 'Unstructured comments collapse into taste.',
        pins: ['structured peer review'],
      },
      {
        text: 'Studio hours as silent making, phones in a box.',
        rationale: 'Attention is the scarce material.',
        pins: ['protected making time'],
      },
      {
        text: 'Assessment against a living rubric the class rewrites mid-term.',
        rationale: 'Criteria should track what the work taught us.',
        pins: ['living rubric'],
      },
      {
        text: 'Invite a practitioner for one hour, not a lecture series.',
        rationale: 'Short contact, then students digest in their own language.',
        pins: ['practitioner visit'],
      },
      {
        text: 'Document the brief as a set of constraints, not a solution picture.',
        rationale: 'If the brief already looks like the answer, making stops.',
        pins: ['constraints not solutions'],
      },
      {
        text: 'Let students refuse a suggested method and write why.',
        rationale: 'Refusal is a design decision.',
        pins: ['method refusal'],
      },
    ],
  },
  {
    id: 'climate',
    tryQuery: 'heat and cooling',
    ideas: [
      {
        text: 'Shade trees along schoolyards as a first cooling layer.',
        rationale: 'Air conditioning is last, not first.',
        pins: ['passive cooling'],
      },
      {
        text: 'Map night-time heat that never leaves dense housing.',
        rationale: 'Daytime peaks hide who cannot recover overnight.',
        pins: ['night heat'],
      },
      {
        text: 'Community cooling rooms that do not feel like clinics.',
        rationale: 'People will not go if the room signals emergency only.',
        pins: ['cooling as public room'],
      },
      {
        text: 'Water fountains that still work during a brownout.',
        rationale: 'Heat risk spikes when power fails.',
        pins: ['resilient water'],
      },
      {
        text: 'Translate heat warnings into the languages of the block.',
        rationale: 'A city alert in one language is not a warning.',
        pins: ['language access'],
      },
      {
        text: 'Green roofs on bus depots, not only on new condos.',
        rationale: 'Public infrastructure should carry the canopy.',
        pins: ['public green roofs'],
      },
      {
        text: 'Track who pays for cooling: tenant, landlord, or city.',
        rationale: 'Adaptation costs leak onto the least mobile households.',
        pins: ['who pays to cool'],
      },
      {
        text: 'A neighbourhood heat steward instead of a dashboard nobody opens.',
        rationale: 'A person on the block beats another app.',
        pins: ['human steward'],
      },
    ],
  },
]
