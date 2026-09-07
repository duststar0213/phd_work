/**
 * DEV-only test packs. One shared topic so look-up and suggest-relations
 * have something to chew on. Each click appends the next wave.
 * Delete this file when the study no longer needs a seed button.
 *
 * Topic: keeping a neighbourhood livable in extreme heat.
 */
export const SEARCH_TEST_TOPIC = 'heat neighbourhood'

export const SEARCH_TEST_PACKS = [
  {
    id: 'heat-space',
    tryQuery: 'shade without air conditioning',
    ideas: [
      {
        text: 'Shade trees along schoolyards as the first cooling layer, not a decorative extra.',
        rationale: 'Air conditioning should be last, not first.',
        pins: ['passive cooling', 'schoolyard shade'],
        pinTop: true,
      },
      {
        text: 'Green roofs on bus depots, not only on new condos.',
        rationale: 'Public infrastructure should carry the canopy.',
        pins: ['public green roofs'],
      },
      {
        text: 'Bus stops with a real roof and a bench that does not bake.',
        rationale: 'Waiting is when heat hits people who cannot drive.',
        pins: ['transit shade', 'waiting in heat'],
        pinTop: true,
      },
      {
        text: 'Play streets that close to cars on the hottest afternoons.',
        rationale: 'Kids need ground they can occupy without asphalt glare.',
        pins: ['play street', 'asphalt heat'],
      },
      {
        text: 'Water fountains that still work during a brownout.',
        rationale: 'Heat risk spikes when power fails.',
        pins: ['resilient water'],
        pinTop: true,
      },
      {
        text: 'A courtyard mist line that neighbours can switch on themselves.',
        rationale: 'Cooling that needs a work order never arrives in time.',
        pins: ['local control', 'mist cooling'],
      },
      {
        text: 'Paint the warehouse roof white even if it looks unfinished.',
        rationale: 'Albedo is cheaper than another chiller.',
        pins: ['cheap albedo'],
      },
      {
        text: 'Keep the north alley unpaved so it can stay damp after rain.',
        rationale: 'Sealed ground cannot give the air any moisture back.',
        pins: ['unsealed ground'],
        pinTop: true,
      },
    ],
  },
  {
    id: 'heat-people',
    tryQuery: 'who cannot recover overnight',
    ideas: [
      {
        text: 'Map night-time heat that never leaves dense housing.',
        rationale: 'Daytime peaks hide who cannot recover overnight.',
        pins: ['night heat', 'dense housing'],
        pinTop: true,
      },
      {
        text: 'Community cooling rooms that do not feel like clinics.',
        rationale: 'People will not go if the room signals emergency only.',
        pins: ['cooling as public room'],
      },
      {
        text: 'A neighbourhood heat steward instead of a dashboard nobody opens.',
        rationale: 'A person on the block beats another app.',
        pins: ['human steward'],
        pinTop: true,
      },
      {
        text: 'Check on older tenants before the third night of a heatwave.',
        rationale: 'The danger is cumulative, not the first hot afternoon.',
        pins: ['elderly check-in', 'third night'],
      },
      {
        text: 'Translate heat warnings into the languages of the block.',
        rationale: 'A city alert in one language is not a warning.',
        pins: ['language access'],
        pinTop: true,
      },
      {
        text: 'Let corner shops be refill points without buying anything.',
        rationale: 'Dignity matters more than a new public kiosk.',
        pins: ['shop as refill', 'no purchase required'],
      },
      {
        text: 'Night-shift workers need a dark, cool room at 11am, not at 9pm.',
        rationale: 'Sleep schedules do not match the cooling-centre hours.',
        pins: ['shift workers', 'daytime sleep'],
      },
      {
        text: 'A festival tent is the wrong template for a cooling room.',
        rationale: 'Celebration furniture tells people this is temporary theatre.',
        pins: ['not a festival tent'],
        pinTop: true,
      },
    ],
  },
  {
    id: 'heat-rules',
    tryQuery: 'who pays for cooling',
    ideas: [
      {
        text: 'Track who pays for cooling: tenant, landlord, or city.',
        rationale: 'Adaptation costs leak onto the least mobile households.',
        pins: ['who pays to cool'],
        pinTop: true,
      },
      {
        text: 'Ban landlords from forbidding a portable AC in a top-floor walk-up.',
        rationale: 'House rules currently protect the building more than the body.',
        pins: ['rental AC rights'],
      },
      {
        text: 'Heat warnings should name a place to go, not only a temperature.',
        rationale: 'A number without a door is not actionable.',
        pins: ['warning with a place'],
        pinTop: true,
      },
      {
        text: 'Do not count a shopping mall as the official cooling centre.',
        rationale: 'People without money to browse do not feel welcome.',
        pins: ['not the mall'],
      },
      {
        text: 'Fund tree pits from the parking budget, not from a leftover greening grant.',
        rationale: 'If shade is optional money, it loses every year.',
        pins: ['parking pays for shade'],
        pinTop: true,
      },
      {
        text: 'A brownout plan for elevators in towers with many older residents.',
        rationale: 'Heat plus stairs is a trap, not an inconvenience.',
        pins: ['elevator backup', 'towers'],
      },
      {
        text: 'Ask whether the new plaza is actually a heat plate before celebrating it.',
        rationale: 'Stone and no trees photograph well and fail in July.',
        pins: ['plaza as heat plate'],
      },
      {
        text: 'Let residents override a night-time lighting scheme that dumps heat.',
        rationale: 'Aesthetic briefs ignore the thermal load of the fixtures.',
        pins: ['lighting heat', 'resident override'],
        pinTop: true,
      },
      {
        text: 'If the school gym is the cooling room, it cannot also be locked at 4pm.',
        rationale: 'Shared civic space fails when the timetable wins.',
        pins: ['gym hours', 'shared civic space'],
      },
    ],
  },
]
