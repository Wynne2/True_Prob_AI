import { Config } from "@remotion/cli/config";

Config.setVideoImageFormat("jpeg");
// ANGLE gives headless Chrome a working WebGL context for the Three.js scenes on Windows.
Config.setChromiumOpenGlRenderer("angle");
