# DnD/PF Dice Roller

A web-based dice roller for Dungeons & Dragons and Pathfinder tabletop RPGs. This is a standalone web application extracted from the [EZBattleMap](https://github.com/yourusername/EZBattleMap) project.

## Features

- **Standard Dice Rolling**: Roll d2, d4, d6, d8, d10, d12, d20, and d100
- **Multiple Dice**: Roll multiple dice at once (1-100 dice per roll)
- **Modifiers**: Add modifiers to your rolls (-999 to +999)
- **Advantage/Disadvantage**: Roll d20 with advantage or disadvantage (5th Edition D&D)
- **Detailed Results**: See individual rolls and totals
- **Results History**: All rolls are saved in the results area
- **Responsive Design**: Works on desktop, tablet, and mobile devices
- **Beautiful UI**: Modern gradient design with smooth animations

## Usage

### Standard Dice Rolling

1. Select the die type (d2, d4, d6, d8, d10, d12, d20, or d100)
2. Set the **Count** (how many dice to roll)
3. Set the **Modifier** (optional, can be positive or negative)
4. Click the **Roll** button

**Example**: Rolling 3d6+4 for ability scores:
- Die: d6
- Count: 3
- Modifier: +4
- Result shows: Individual rolls, sum, and final total

### Advantage/Disadvantage

For D&D 5th Edition advantage/disadvantage mechanics:

- Click **Roll with Advantage** to roll 2d20 and take the higher result
- Click **Roll with Disadvantage** to roll 2d20 and take the lower result

### Keyboard Shortcuts

- Press **Enter** after typing in Count or Modifier to roll that die immediately

### Clearing Results

Click the **Clear Results** button to clear the results history.

## Installation

### Option 1: Direct Use (No Installation)

Simply open `index.html` in any modern web browser:

1. Double-click `index.html`
2. Or right-click → Open With → Your favorite browser

### Option 2: Web Hosting

To host on a web server:

1. Upload all files to your web server:
   - `index.html`
   - `style.css`
   - `script.js`

2. Access via your domain (e.g., `https://diceroller.davidkendig.info`)

### Option 3: Local Web Server

For testing with a local server:

```bash
# Using Python 3
python -m http.server 8000

# Using Node.js http-server
npx http-server

# Using PHP
php -S localhost:8000
```

Then open `http://localhost:8000` in your browser.

## Files

- **index.html** - Main HTML structure
- **style.css** - Styling and responsive design
- **script.js** - Dice rolling logic and interactivity
- **README.md** - This documentation

## Browser Compatibility

Works on all modern browsers:
- Chrome/Edge (recommended)
- Firefox
- Safari
- Opera

## Features Breakdown

### Standard Dice Section

Supports all common RPG dice types:
- **d2** - Coin flip (1-2)
- **d4** - Four-sided die (1-4)
- **d6** - Six-sided die (1-6)
- **d8** - Eight-sided die (1-8)
- **d10** - Ten-sided die (1-10)
- **d12** - Twelve-sided die (1-12)
- **d20** - Twenty-sided die (1-20)
- **d100** - Percentile die (1-100)

### Advantage/Disadvantage

Implements D&D 5th Edition mechanics:
- **Advantage**: Roll 2d20, take the higher result
- **Disadvantage**: Roll 2d20, take the lower result

Perfect for:
- Attack rolls with advantage/disadvantage
- Saving throws
- Ability checks

## Use Cases

- **Character Creation**: Roll ability scores (3d6, 4d6 drop lowest, etc.)
- **Combat**: Roll attack damage (1d8+3, 2d6+5, etc.)
- **Skill Checks**: Roll d20 with modifiers
- **Initiative**: Roll d20 for turn order
- **Loot Tables**: Roll d100 for random treasure
- **Quick Rolls**: Fast dice rolling without physical dice
- **Online Play**: Share screen during virtual tabletop sessions

## Technical Details

- Pure JavaScript (no dependencies)
- Vanilla CSS3 with flexbox and grid
- Mobile-first responsive design
- Cryptographically secure random number generation (Math.random)
- Accessible keyboard navigation

## Credits

**Author**: David Kendig
**Copyright**: 2025
**Source**: Extracted from EZBattleMap project
**License**: MIT License (see parent project)

## Related Projects

- [EZBattleMap](https://github.com/yourusername/EZBattleMap) - Dual-screen battlemap tool with fog of war
- [DavidKendig.info](https://davidkendig.info) - Portfolio and web projects

## Version History

### Version 1.0.0 (2025-01-03)
- Initial release
- Extracted from EZBattleMap v1.25.12
- Standard dice rolling (d2-d100)
- Advantage/Disadvantage support
- Responsive design
- Results history

## Contributing

This is a personal project, but suggestions are welcome! Open an issue on the main EZBattleMap repository.

## Support

For questions or issues:
- Email: contact@davidkendig.info
- GitHub: [EZBattleMap Issues](https://github.com/yourusername/EZBattleMap/issues)

---

Enjoy rolling dice! 🎲
