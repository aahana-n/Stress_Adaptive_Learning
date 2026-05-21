# Stress_Adaptive_Learning
🧠 Project Overview

The Stress-Adaptive Learning System is an AI-driven neurophysiological learning platform that dynamically adapts question difficulty and learning flow based on the user's realtime cognitive and physiological state.

The system integrates:

-EEG-based brain activity monitoring
-Heart rate and HRV analysis
-AI-generated adaptive assessments
-Performance analytics
-Realtime physiological visualization

to create a personalized and cognitively-aware learning experience.
Unlike conventional adaptive learning systems that rely only on answer correctness, this platform continuously analyzes physiological indicators such as:

-EEG Beta/Alpha ratio
-HRV RMSSD
-Heart Rate trends
-Response performance

to estimate stress and cognitive load in realtime.

The system then intelligently modifies:

-question difficulty
-assessment pacing
-learning complexity

to optimize user engagement and performance.

🚀 Key Features

1. 🧬 Realtime Physiological Monitoring
   
The system continuously acquires and visualizes physiological data including EEG Signals. Using the BioAmp EXG Pill, the platform captures EEG activity and computes:

-Alpha band power
-Beta band power
-Theta band power
-Beta/Alpha ratio (BAR)
-Cardiovascular Metrics

Using the MAX30102 PPG sensor, the system measures:

-Heart Rate (BPM)
-HRV RMSSD

These metrics are used to infer:

-cognitive load
-stress level
-relaxation state
-focus intensity

2. 🧠 Intelligent Stress Detection Engine
   
The stress classification engine combines:

-HRV RMSSD
-Heart Rate
-EEG Beta/Alpha ratio

to dynamically classify users into Low/Moderate/High Stress

The system uses threshold-based multimodal physiological fusion for realtime cognitive-state estimation.

3. 🤖 AI-Based Adaptive Question Generation
   

The platform integrates AI-generated MCQs using the OpenAI API.

Question difficulty adapts dynamically based on:

-physiological stress
-user performance
-calibration assessment
-subject selection
-Difficulty Mapping
-Stress Level	Generated Questions
-Low Stress	Hard / Challenging
-Moderate Stress	Intermediate
-High Stress	Easier Conceptual Questions

This prevents:

-cognitive overload
-learner fatigue
-excessive frustration

while maintaining engagement.

4. 📚 Subject-Adaptive Learning
   
Before beginning the assessment, users select from JEE/NEET/COMEDK/KCET

After an initial calibration quiz, subject-specific adaptive assessments are generated dynamically.

Supported subject flows include:

-Physics
-Chemistry
-Mathematics
-Biology

5. 🧠 Cognitive Calibration System
   
The platform includes a preliminary calibration assessment to estimate:

-baseline cognitive ability
-response behavior
-performance consistency

This enables more personalized adaptive difficulty scaling during the session.

6. 📊 Advanced Analytics Dashboard
   
The web dashboard provides realtime visualization of:

-Heart Rate
-HRV RMSSD
-EEG Beta/Alpha Ratio
-EEG Band Powers
-Stress Level
-Accuracy
-Streak
-Session Time

Interactive graphs continuously update using live physiological data streams.

7. 📈 Historical Session Review
   
Users can review previous sessions including:

-stress patterns
-physiological trends
-performance history
-answered questions
-accuracy metrics

This allows long-term cognitive and performance tracking.

8. 💾 Data Export System
   
The platform supports downloadable JSON logs and CSV datasets containing:

-physiological data
-question history
-stress labels
-timestamps
-performance metrics

These exports enable:

-research analysis
-external visualization
-machine learning dataset generation

9. 📡 Wireless Embedded Hardware Architecture
    
The wearable prototype is built using:

-ESP32-C3 SuperMini
-BioAmp EXG Pill
-MAX30102
-Gel EEG Electrodes
-5V Power Bank

The ESP32 performs sensor acquisition, wireless transmission and realtime streaming to the web dashboard.

10. 🌐 Realtime Web-Based Interface
    
The frontend dashboard provides:

-live physiological monitoring
-adaptive assessments
-realtime graph updates
-AI-generated MCQs
-session analytics

through a browser-based interface.


The uniqueness of this project lies in its integration of:

-wearable biosignal acquisition
-AI-driven adaptive learning
-cognitive-state awareness
-realtime physiological analytics

into a single closed-loop adaptive educational platform.

Unlike traditional e-learning systems, the platform does not rely solely on answer correctness but also incorporates physiological state into learning adaptation.


