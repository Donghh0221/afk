# React Native (Expo) Project

This is a React Native project using Expo, managed by AFK.

## Tech Stack

- Expo SDK 52+
- React Native
- expo-router (file-based routing)
- TypeScript
- NativeWind (Tailwind CSS for React Native) — optional

## Guidelines

- Use expo-router with file-based routing (`app/` directory)
- Prefer functional components with hooks
- Use TypeScript for all new files
- Use `npx expo start --tunnel` for remote preview via Expo Go
- Run `npx expo export` to verify the bundle before completing

## Getting Started

If this is a fresh project, initialize with:
```bash
npx create-expo-app@latest . --template tabs
```

## Testing on Device

Use `/tunnel` in the AFK session to start an Expo tunnel.
Scan the QR code or open the `exp://` link in Expo Go.
