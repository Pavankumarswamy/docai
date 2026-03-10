document.addEventListener('DOMContentLoaded', () => {
    // Scroll Reveal functionality
    const reveals = document.querySelectorAll('.reveal');
    const revealOnScroll = () => {
        const triggerBottom = window.innerHeight * 0.85;
        reveals.forEach(reveal => {
            const revealTop = reveal.getBoundingClientRect().top;
            if (revealTop < triggerBottom) {
                reveal.classList.add('active');
            }
        });
    };

    window.addEventListener('scroll', revealOnScroll);
    revealOnScroll(); // Initial check

    // Typing animation for the mock terminal
    const typingElements = document.querySelectorAll('.typing');

    const typeText = (element, text, delay = 100) => {
        let i = 0;
        const timer = setInterval(() => {
            if (i < text.length) {
                element.textContent += text.charAt(i);
                i++;
            } else {
                clearInterval(timer);
                // Trigger terminal response after command is "typed"
                if (element.closest('#terminal-content')) {
                    setTimeout(addTerminalResponse, 500);
                }
            }
        }, delay);
    };

    const addTerminalResponse = () => {
        const terminalContent = document.getElementById('terminal-content');
        const responses = [
            { text: 'Initializing GGU AI Healing Engine...', color: '#a6e3a1' },
            { text: 'Scanning for repository failures...', color: '#cdd6f4' },
            { text: 'Failure detected in: [tests/auth.test.js]', color: '#f38ba8' },
            { text: 'Analyzing failure with NVIDIA LLM...', color: '#cdd6f4' },
            { text: 'Generating fix...', color: '#00ff88' },
            { text: 'Patch applied successfully. Retesting...', color: '#a6e3a1' },
            { text: 'All tests passed. Pipeline healed.', color: '#00ff88', bold: true }
        ];

        let index = 0;
        const interval = setInterval(() => {
            if (index < responses.length) {
                const line = document.createElement('div');
                line.className = 'line animate-in';
                line.style.color = responses[index].color;
                if (responses[index].bold) line.style.fontWeight = '800';
                line.textContent = responses[index].text;
                terminalContent.appendChild(line);
                index++;
            } else {
                clearInterval(interval);
            }
        }, 800);
    };

    // Start typing when terminal is in view
    const observer = new IntersectionObserver((entries) => {
        entries.forEach(entry => {
            if (entry.isIntersecting) {
                const typingTarget = entry.target.querySelector('.typing');
                if (typingTarget && !typingTarget.dataset.started) {
                    typingTarget.dataset.started = "true";
                    typeText(typingTarget, typingTarget.getAttribute('data-text'));
                }
            }
        });
    }, { threshold: 0.5 });

    const terminalSection = document.querySelector('.terminal-demo');
    if (terminalSection) observer.observe(terminalSection);

    // 3D Tilt and Magnetic Glow for Feature Cards
    const cards = document.querySelectorAll('.tilt-card');

    cards.forEach(card => {
        const glow = card.querySelector('.glow');

        card.addEventListener('mousemove', (e) => {
            const rect = card.getBoundingClientRect();
            const x = e.clientX - rect.left;
            const y = e.clientY - rect.top;

            // Calculate tilt
            const centerX = rect.width / 2;
            const centerY = rect.height / 2;
            const rotateX = (y - centerY) / 10;
            const rotateY = (centerX - x) / 10;

            card.style.transform = `perspective(1000px) rotateX(${rotateX}deg) rotateY(${rotateY}deg) scale3d(1.02, 1.02, 1.02)`;

            // Update glow position
            if (glow) {
                glow.style.left = `${x}px`;
                glow.style.top = `${y}px`;
            }
        });

        card.addEventListener('mouseleave', () => {
            card.style.transform = `perspective(1000px) rotateX(0deg) rotateY(0deg) scale3d(1, 1, 1)`;
        });
    });
});
