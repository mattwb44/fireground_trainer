# TODO 

1. Define database schema and relationships
    - 
2. Implement roles and permissions (Admin, Training Chief, Instructor, Participant)
3. Add authentication (magic-link + password) for instructor/admin roles
4. Security checks (access control, input validation)
5. Basic admin screens for user management
6. Initial seed data for demo scenarios
7. Implement scenario workflow: Draft → Submitted → Approved → Archived
8. Archiving behavior for sessions/scenarios
9. Build Scenario Library with tabs: Official, Practice, Mine, Submitted
10. Add scenario creation UI with question type selection
11. Question types for MVP:
    - Auto-scored checklist
    - Short Answer (Participant's Answers Matched and Scored to Creator's Answer)
    - Discussion-only open-ended (non-graded)
12. Add session creation with QR/join code
13. Participant join flow with shift selection + anonymous/name choice
14. Answer submission and storage
15. Instructor session dashboard (live list of submissions)
16. Answer reveal controls (manual + random)
17. Review + approve answers before report generation
18. Exclude/flag answers with audit log
19. Reporting:
    - Per session
    - Per shift
    - Group vs group
    - Over-time trend view
20. Export reports to CSV (PDF later)
21. Create branded homepage
    - Temporary site name: Blitzfire Training
    - Professional landing page design
    - Login and Create Account buttons in top right
    - Homepage acts as a router into category pages
22. Add top-level site navigation for training categories
    - Training Scenarios dropdown
    - Fireground Training
    - Motor Vehicle Accidents
    - Emergency Medical Services
    - Keep the navigation bar visible at the top across category pages
23. Build category landing pages
    - Fireground Training page listing available fireground scenarios
    - Placeholder MVA scenarios page
    - Placeholder EMS scenarios page
24. Expand scenario browsing UX
    - Let users choose a training category first
    - Route users from the homepage to Fireground, MVA, or EMS pages
    - Category pages are the main end-user browsing flow
    - Keep scenario library behavior consistent with the selected category where applicable
25. Add left-side filter panel for scenario pages
    - Official
    - Instructor Made
    - User Made
    - Leave room for future filters like New, Top Rated, and Top of the Month
    - Define creator roles before exposing creator filters heavily
    - User Made = created by a user who is not labeled/authorized as an Instructor
    - Instructor Made = created by an instructor-authorized creator
26. Seed content for category pages
    - Existing three fireground scenario templates
    - Smoke detector scenario
    - Placeholder empty-state content for MVA and EMS
27. Add scenario engagement/ranking system
    - Like-only system for now
    - One vote per user per scenario
    - Only users with a completed scenario submission can rate
    - Users can change their vote later
    - Store popularity totals
    - Surface popular / most liked scenarios
28. Add scenario helpfulness metrics
    - Removed for now in favor of a simpler Like-only model
29. Redesign host facilitation board
    - Make the board page the primary host workspace during active sessions
    - Show active session join code and QR directly on the board page
    - Let the host keep the scenario image, tokens, and questions visible while facilitating
    - Replace submission-level review with question-grouped answer review
    - Show answers under each question in a format like:
      - Question text
      - Anonymous Person 1: answer
      - Anonymous Person 2: answer
    - Support stable anonymous labels per session for review purposes
    - Add near-live updates for host answer review while the session is active
    - Replace whole-submission reveal with per-question cherry-pick reveal
    - Allow the host to reveal one chosen answer per question, whether from the same participant or different participants
    - Update the participant-facing revealed-answer panel to reflect per-question reveals instead of one full submission
30. Harden account creation and onboarding
    - Convert Create Account from placeholder to real flow
    - Decide which roles can self-register vs admin-only assignment
    - Add email verification or equivalent safe activation flow
    - Make sign-in / join / participant identity behavior easier to understand
31. Expand authentication and session security
    - Review login/logout/session handling end to end
    - Tighten CSRF and state-changing route protections
    - Recheck role boundary assumptions across instructor/chief/admin/participant flows
    - Add guardrails for stale session, stale participant, and stale host workspace state
32. Add schema migration strategy
    - Stop relying only on runtime compatibility patches for growth
    - Introduce a safer migration path for new tables/columns
    - Document local upgrade steps for future schema changes
33. Broaden automated test coverage
    - Add regression coverage for auth/account flows
    - Add coverage for host board reveal/review edge cases
    - Add negative-path tests for access control and invalid state transitions
    - Add smoke coverage for reports after moderation/reveal changes
34. Harden board/session/submission edge cases
    - Empty session behavior
    - Session switching between multiple active host contexts
    - Rejoin behavior for participants
    - Excluded/flagged/reinstated answer handling across host and participant views
    - Refresh/live-update race conditions
35. Improve host review UX
    - Make board review actions clearer and faster to use
    - Add better visual separation between revealed, approved, flagged, excluded, and pending answers
    - Reduce page-jumping between board and submission detail where possible
    - Make note-taking/review status changes more ergonomic
36. Improve participant experience
    - Better join instructions and validation feedback
    - Clearer submission saved/attempt messaging
    - Better participant-facing revealed answer readability on mobile
    - Review participant flow for confusion around named vs anonymous identity
37. Polish scenario/category browsing
    - Improve empty states and filter clarity
    - Add more consistent scenario metadata presentation
    - Revisit ranking/popularity presentation after more content exists
    - Prepare for future New / Top Rated / Top of the Month filters
38. Prepare deployment and environment configuration
    - Document required env vars and safe defaults
    - Separate demo/seed behavior from production expectations
    - Add deployment/startup notes for local pilot use
    - Review file paths, static assets, and DB config assumptions
39. Add operational/admin safety checks
    - Better admin visibility into user/session/scenario state
    - Safer handling for archived/inactive content
    - Logging/audit improvements for important moderator/admin actions
    - Add a pre-pilot checklist for data safety and rollback readiness
40. Release readiness pass
    - Full end-to-end Fireground regression pass
    - Security review pass
    - UX polish pass
    - Documentation pass
    - Final MVP-to-pilot ship checklist

## Future Ideas

- Add contributor badges such as Top Rated Submitter, Certified Instructor, Certified Firefighter
- Certified badges should be manually admin-verified
- Consider reputation and trust systems tied to scenario quality and engagement
