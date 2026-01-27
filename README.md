# Fireground Trainer

Fireground Trainer is a web-based training application designed to help firefighters practice fireground size-up, tactics, and decision-making using realistic residential scenarios.

The goal of this project is to create an interactive, visual way to think through fireground operations — not just answer questions, but **place resources, visualize conditions, and justify decisions**.

---

## Current Features (MVP)

- Scenario-based training with dispatch information
- Residential structure images used as a training board
- Draggable, image-based tokens for:
  - Fire
  - Smoke
  - Wind direction
  - Ladders
  - Attack teams
  - RIT teams
- Tokens can be:
  - Dragged and repositioned
  - Rotated using a rotation handle
  - Deleted using an explicit delete button
- Tokens only show controls when selected (to prevent accidental actions)
- Scenario questions focused on:
  - Scene size-up
  - Hose line selection and PDP
  - GPM calculations
  - Offensive vs defensive transitions
  - Fireground tactics
- Progressive Web App (PWA) setup for future mobile/tablet use

---

## Tech Stack

- **Backend:** Python, Flask
- **Frontend:** HTML, CSS, Vanilla JavaScript
- **Architecture:** Server-rendered templates with client-side interaction
- **Platform:** Desktop and mobile browser compatible
- **Version Control:** Git / GitHub

---

## Project Status

This project is currently in **early MVP development**.

Right now, the focus is on:
- Visual fireground interaction
- Core training workflow
- Usable UI for firefighters

Future iterations will expand realism and persistence.

---

## Planned Features

- Multiple fire/smoke placement zones per structure
- Wind direction effects and flow path visualization
- Alpha/Bravo/Charlie/Delta side labeling
- Scenario randomization
- Saving and reviewing token layouts
- Paramedic/EMS decision-making scenarios
- Multi-story and commercial structures
- Street View–style local structure images
- Instructor review / discussion mode

---

## Why This Project Exists

Fireground training often lives on whiteboards, paper diagrams, or mental reps.  
This project aims to bridge the gap between **tactical knowledge and visual decision-making**, using tools that feel natural and interactive.

The long-term vision is a lightweight, realistic training aid that complements hands-on drills and tabletop discussions.

---

## Disclaimer

This application is for **training and discussion purposes only**.  
It is not intended to replace department SOPs, formal training, or incident command judgment.

---

## Author

Built by a firefighter/paramedic exploring how software can support better training, decision-making, and preparation on the fireground.