// Dice types to display
const diceTypes = [2, 3, 4, 6, 8, 10, 12, 20, 100];

// Initialize the dice roller on page load
document.addEventListener('DOMContentLoaded', function() {
    initializeDiceGrid();
    initializeSR4DiceGrid();
    // Set current year in footer
    document.getElementById('currentYear').textContent = new Date().getFullYear();
});

/**
 * Initialize the standard dice grid with all dice types
 */
function initializeDiceGrid() {
    const diceGrid = document.getElementById('standardDice');

    diceTypes.forEach(die => {
        const diceRow = createDiceRow(die);
        diceGrid.appendChild(diceRow);
    });
}

/**
 * Create a dice row element for a specific die type
 * @param {number} sides - Number of sides on the die
 * @returns {HTMLElement} The dice row element
 */
function createDiceRow(sides) {
    const row = document.createElement('div');
    row.className = 'dice-row';

    // Die label (e.g., "d20")
    const label = document.createElement('div');
    label.className = 'die-label';
    label.textContent = `d${sides}`;

    // Count input group
    const countGroup = document.createElement('div');
    countGroup.className = 'input-group';
    const countLabel = document.createElement('label');
    countLabel.textContent = 'Count:';
    const countInput = document.createElement('input');
    countInput.type = 'number';
    countInput.value = 1;
    countInput.min = 1;
    countInput.max = 100;
    countInput.id = `count-d${sides}`;
    countGroup.appendChild(countLabel);
    countGroup.appendChild(countInput);

    // Modifier input group
    const modGroup = document.createElement('div');
    modGroup.className = 'input-group';
    const modLabel = document.createElement('label');
    modLabel.textContent = 'Modifier:';
    const modInput = document.createElement('input');
    modInput.type = 'number';
    modInput.value = 0;
    modInput.min = -999;
    modInput.max = 999;
    modInput.id = `mod-d${sides}`;
    modGroup.appendChild(modLabel);
    modGroup.appendChild(modInput);

    // Roll button
    const rollBtn = document.createElement('button');
    rollBtn.className = 'btn btn-roll';
    rollBtn.textContent = 'Roll';
    rollBtn.onclick = () => rollDice(sides);

    // Add enter key support for inputs
    countInput.addEventListener('keypress', (e) => {
        if (e.key === 'Enter') rollDice(sides);
    });
    modInput.addEventListener('keypress', (e) => {
        if (e.key === 'Enter') rollDice(sides);
    });

    row.appendChild(label);
    row.appendChild(countGroup);
    row.appendChild(modGroup);
    row.appendChild(rollBtn);

    return row;
}

/**
 * Roll standard dice
 * @param {number} sides - Number of sides on the die
 */
function rollDice(sides) {
    const count = parseInt(document.getElementById(`count-d${sides}`).value);
    const modifier = parseInt(document.getElementById(`mod-d${sides}`).value);

    // Validate inputs
    if (count < 1 || count > 100) {
        appendResult('Error: Count must be between 1 and 100\n\n');
        return;
    }

    if (modifier < -999 || modifier > 999) {
        appendResult('Error: Modifier must be between -999 and 999\n\n');
        return;
    }

    let result = '';
    const modifierStr = modifier >= 0 ? `+${modifier}` : `${modifier}`;
    result += `Rolling ${count}d${sides}${modifierStr}:\n`;

    let total = 0;
    const rolls = [];

    // Roll each die
    for (let i = 0; i < count; i++) {
        const roll = Math.floor(Math.random() * sides) + 1;
        total += roll;
        rolls.push(roll);
    }

    result += `Rolls: ${rolls.join(', ')}\n`;

    const finalTotal = total + modifier;
    result += `Total: ${finalTotal}`;

    if (modifier !== 0) {
        result += ` (${total}${modifierStr})`;
    }

    result += '\n\n';

    appendResult(result);
}

/**
 * Roll d20 with advantage or disadvantage
 * @param {boolean} isAdvantage - True for advantage, false for disadvantage
 */
function rollAdvantage(isAdvantage) {
    const roll1 = Math.floor(Math.random() * 20) + 1;
    const roll2 = Math.floor(Math.random() * 20) + 1;

    let result;
    let type;

    if (isAdvantage) {
        result = Math.max(roll1, roll2);
        type = 'Advantage';
    } else {
        result = Math.min(roll1, roll2);
        type = 'Disadvantage';
    }

    let output = '';
    output += `Rolling d20 with ${type}:\n`;
    output += `Rolls: ${roll1}, ${roll2}\n`;
    output += `Result: ${result}\n\n`;

    appendResult(output);
}

/**
 * Append text to the results area
 * @param {string} text - Text to append
 */
function appendResult(text) {
    const resultsArea = document.getElementById('resultsArea');
    resultsArea.value += text;
    // Auto-scroll to bottom
    resultsArea.scrollTop = resultsArea.scrollHeight;
}

/**
 * Clear all results
 */
function clearResults() {
    const resultsArea = document.getElementById('resultsArea');
    resultsArea.value = '';
}

// ==================== TAB SWITCHING ====================

/**
 * Switch between tabs
 * @param {string} tabId - The ID of the tab to switch to
 */
function switchTab(tabId) {
    // Hide all tab contents
    const tabContents = document.querySelectorAll('.tab-content');
    tabContents.forEach(content => {
        content.classList.remove('active');
    });

    // Deactivate all tab buttons
    const tabBtns = document.querySelectorAll('.tab-btn');
    tabBtns.forEach(btn => {
        btn.classList.remove('active');
    });

    // Show selected tab content
    document.getElementById(tabId).classList.add('active');

    // Activate corresponding tab button
    const activeBtn = document.querySelector(`[onclick="switchTab('${tabId}')"]`);
    if (activeBtn) {
        activeBtn.classList.add('active');
    }
}

// ==================== SR4+ DICE ROLLER ====================

/**
 * Initialize the SR4+ dice grid with numbers 1-30
 */
function initializeSR4DiceGrid() {
    const sr4Grid = document.getElementById('sr4DiceGrid');

    for (let i = 1; i <= 30; i++) {
        const btn = document.createElement('button');
        btn.className = 'sr4-dice-btn';
        btn.textContent = i;
        btn.onclick = () => rollSR4Dice(i);
        sr4Grid.appendChild(btn);
    }
}

/**
 * Roll SR4+ dice (d6s with hit/miss counting)
 * @param {number} count - Number of d6 dice to roll
 */
function rollSR4Dice(count) {
    const rolls = [];
    let hits = 0;
    let misses = 0;

    // Roll each d6
    for (let i = 0; i < count; i++) {
        const roll = Math.floor(Math.random() * 6) + 1;
        rolls.push(roll);

        // Count hits (5 and 6)
        if (roll === 5 || roll === 6) {
            hits++;
        }

        // Count misses (1)
        if (roll === 1) {
            misses++;
        }
    }

    // Update counters
    document.getElementById('hitsCount').textContent = hits;
    document.getElementById('missesCount').textContent = misses;

    // Format output
    let output = '';
    output += `Rolling ${count}d6 (SR4+):\n`;
    output += `Rolls: ${rolls.join(', ')}\n`;
    output += `Hits: ${hits} | Misses: ${misses}\n\n`;

    appendSR4Result(output);
}

/**
 * Append text to the SR4+ results area
 * @param {string} text - Text to append
 */
function appendSR4Result(text) {
    const resultsArea = document.getElementById('sr4ResultsArea');
    resultsArea.value += text;
    // Auto-scroll to bottom
    resultsArea.scrollTop = resultsArea.scrollHeight;
}

/**
 * Clear SR4+ results
 */
function clearSR4Results() {
    const resultsArea = document.getElementById('sr4ResultsArea');
    resultsArea.value = '';
    document.getElementById('hitsCount').textContent = '0';
    document.getElementById('missesCount').textContent = '0';
}
