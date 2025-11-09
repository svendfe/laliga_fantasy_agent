"""
Enhanced Fantasy Football Agent
Analyzes team performance, suggests transfers, and provides fixture analysis
"""

import json
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from functools import lru_cache

# Updated to import from the package
from . import fantasy_scrapper
# This import assumes download_pipeline.py is still run from the root
from download_pipeline import main as data_downloader


# ============================================================================
# DATA MODELS
# ============================================================================
@dataclass
class TeamStats:
    """Data class for team statistics."""
    games_played: int = 0
    goals_scored_home: int = 0
    goals_against_home: int = 0
    goals_scored_away: int = 0
    goals_against_away: int = 0
    wins_home: int = 0
    wins_away: int = 0
    losses_home: int = 0
    losses_away: int = 0
    draws_home: int = 0
    draws_away: int = 0

    @property
    def total_goals_scored(self) -> int:
        return self.goals_scored_home + self.goals_scored_away

    @property
    def total_goals_against(self) -> int:
        return self.goals_against_home + self.goals_against_away

    @property
    def total_wins(self) -> int:
        return self.wins_home + self.wins_away

    @property
    def total_losses(self) -> int:
        return self.losses_home + self.losses_away

    @property
    def total_draws(self) -> int:
        return self.draws_home + self.draws_away

    @property
    def points(self) -> int:
        return self.total_wins * 3 + self.total_draws

    def to_dict(self) -> Dict:
        """Convert to dictionary with computed properties."""
        base_dict = asdict(self)
        base_dict.update({
            'total_goals_scored': self.total_goals_scored,
            'total_goals_against': self.total_goals_against,
            'total_wins': self.total_wins,
            'total_losses': self.total_losses,
            'total_draws': self.total_draws,
            'points': self.points,
            'goal_difference': self.total_goals_scored - self.total_goals_against
        })
        return base_dict

@dataclass
class ScrapedPlayerData:
    """Web-scraped player data from fantasy sources"""
    jerarquia: Optional[int] = None
    play_probability: Optional[float] = None
    form_arrow: Optional[int] = None
    injury_risk: Optional[str] = None
    
    INJURY_RISK_SCORES = {
        "Ironman": 1.3,
        "Bajo": 1.0,
        "Medio": 0.5,
        "Alto": 0.1
    }
    
    def get_injury_risk_score(self) -> float:
        """Convert injury risk to numeric score (higher is better)"""
        if not self.injury_risk:
            return 0.7
        return self.INJURY_RISK_SCORES.get(self.injury_risk, 0.7)
    
    def get_jerarquia_score(self) -> float:
        """Normalize hierarchy score to 0-1 range"""
        if not self.jerarquia:
            return 0.5
        return (7 - self.jerarquia) / 6.0
    
    def get_form_score(self) -> float:
        """Normalize form arrow to 0-1 range"""
        if not self.form_arrow:
            return 0.5
        return 5.0 - (self.form_arrow / 5.0)

@dataclass
class Player:
    """Player model with stats and market information"""
    id: str
    nickname: str
    position_id: int
    team_id: str
    team_name: str
    points: int
    average_points: float
    last_season_points: Optional[int]
    market_value: int
    player_status: str
    last_3_weeks: List[int] = field(default_factory=list)
    minutes_last_3: List[int] = field(default_factory=list)
    is_on_market: bool = False
    owned_by: Optional[str] = None
    buyout_clause: Optional[int] = None
    buyout_locked_until: Optional[datetime] = None
    sale_price: Optional[int] = None
    scraped_data: Optional[ScrapedPlayerData] = None
    _slug: Optional[str] = None
    
    POSITION_NAMES = {1: "GK", 2: "DF", 3: "MF", 4: "FW", 5: "COACH"}
    
    def get_slug(self) -> str:
        """
        Generate URL-friendly slug from player name.
        NOTE: This is a fallback. The ScraperManager should be the
        primary source of slug generation as it has the mapping file.
        """
        if self._slug:
            return self._slug
            
        full_name = self.nickname
            
        slug = (full_name.lower()
                .replace(' ', '-')
                .replace('á', 'a').replace('é', 'e')
                .replace('í', 'i').replace('ó', 'o')
                .replace('ú', 'u').replace('ñ', 'n')
                .replace('.', '').replace("'", ''))
        
        self._slug = slug
        return slug
    
    def price_in_millions(self) -> float:
        """Convert market value to millions"""
        return self.market_value / 1_000_000
    
    def points_per_game(self) -> float:
        """Average points per game"""
        return self.average_points
    
    def form_last_3(self) -> float:
        """Recent form based on last 3 matches"""
        if not self.last_3_weeks:
            return self.average_points
        return sum(self.last_3_weeks) / len(self.last_3_weeks)
    
    def minutes_reliability(self) -> float:
        """Playing time reliability (0-1 scale)"""
        if not self.minutes_last_3:
            return 0.5
        avg_minutes = sum(self.minutes_last_3) / len(self.minutes_last_3)
        return min(avg_minutes / 90.0, 1.0)
    
    def is_available(self) -> bool:
        """Check if player is available (not injured/suspended)"""
        return self.player_status == "ok"
    
    def is_transferable(self, current_time: datetime = None) -> bool:
        """Check if player can be transferred"""
        if current_time is None:
            current_time = datetime.now(timezone.utc)
            
        if self.is_on_market:
            return True
            
        if self.buyout_locked_until and self.buyout_locked_until < current_time:
            return True
            
        return False
    
    def get_acquisition_cost(self) -> float:
        """Get cost to acquire player in millions"""
        if self.is_on_market and self.sale_price:
            return self.sale_price / 1_000_000
            
        if self.buyout_clause:
            return self.buyout_clause / 1_000_000
            
        return float('inf')

@dataclass
class Fixture:
    """Match fixture information"""
    match_id: str
    match_date: datetime
    home_team_id: str
    home_team_name: str
    away_team_id: str
    away_team_name: str
    home_score: Optional[int] = None
    away_score: Optional[int] = None
    match_state: int = 0

@dataclass
class Team:
    """Fantasy team model"""
    team_id: str
    manager_name: str
    players: List[Player]
    team_value: int
    team_points: int
    team_money: Optional[int]
    position: int
    
    def total_value_millions(self) -> float:
        """Total team value in millions"""
        return self.team_value / 1_000_000
    
    def budget_millions(self) -> float:
        """Available budget in millions"""
        if self.team_money is None:
            return 0.0
        return self.team_money / 1_000_000

# ============================================================================
# UTILITIES
# ============================================================================

class PlayerMapper:
    """Maps fantasy nicknames to real player names"""
    
    def __init__(self, mapping_path: str):
        self.mapping_path = Path(mapping_path)
        
        if not self.mapping_path.exists():
            raise FileNotFoundError(
                f"Missing {mapping_path}. Create it with empty JSON {{}}"
            )
            
        with open(self.mapping_path, "r", encoding="utf-8") as f:
            self.name_mapping = json.load(f)
    
    def get_real_name(self, fantasy_name: str) -> Optional[str]:
        """Get real name from fantasy nickname"""
        return self.name_mapping.get(fantasy_name)


class ScraperManager:
    """Manages web scraping for player data"""
    
    def __init__(self, cache_dir: str = "./scrapper_cache", mapping_path: str = "config/name_mapping.json"):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(exist_ok=True)
        self._failed_scrapes = set()
        try:
            self.mapper = PlayerMapper(mapping_path)
        except FileNotFoundError:
            print(f"⚠️  {mapping_path} not found. Slug generation will use fallbacks.")
            self.mapper = None
    
    @lru_cache(maxsize=100)
    def get_player_data(self, player_slug: str) -> Optional[ScrapedPlayerData]:
        """Fetch and cache player data from web"""
        if player_slug in self._failed_scrapes:
            return None
            
        try:
            scraper = fantasy_scrapper.FantasyScraper(player_slug)
            data = scraper.get_player_info()
            
            jerarquia = None
            if data.get('jerarquia'):
                try:
                    jerarquia = int(data['jerarquia'])
                except (ValueError, TypeError):
                    pass
            
            return ScrapedPlayerData(
                jerarquia=jerarquia,
                play_probability=data.get('probabilities'),
                form_arrow=data.get('arrow_numbers'),
                injury_risk=data.get('riesgo_lesion')
            )
            
        except Exception as e:
            print(f"⚠️  Failed to scrape {player_slug}: {str(e)}")
            self._failed_scrapes.add(player_slug)
            return None

    def _get_slug_for_player(self, player: Player) -> str:
        """Generate URL-friendly slug from player name"""
        if player._slug:
            return player._slug
        
        full_name = player.nickname
        if self.mapper:
            mapped_name = self.mapper.get_real_name(player.nickname)
            if mapped_name:
                full_name = mapped_name
        
        slug = (full_name.lower()
                .replace(' ', '-')
                .replace('á', 'a').replace('é', 'e')
                .replace('í', 'i').replace('ó', 'o')
                .replace('ú', 'u').replace('ñ', 'n')
                .replace('.', '').replace("'", ''))
        
        player._slug = slug
        return slug

    def enrich_player(self, player: Player) -> Player:
        """Add scraped data to player object"""
        if player.position_id == 5:  # Skip coaches
            return player
            
        slug = self._get_slug_for_player(player)
        scraped = self.get_player_data(slug)
        
        if scraped:
            player.scraped_data = scraped
            
        return player
    
    def enrich_players_batch(
        self,
        players: List[Player],
        max_to_scrape: int = 1000
    ) -> List[Player]:
        """Enrich multiple players with web data"""
        print(f"\n🔍 Enriching player data (max {max_to_scrape})...")
        
        scraped_count = 0
        for player in players:
            if player.position_id == 5:
                continue
                
            if scraped_count >= max_to_scrape:
                break
                
            self.enrich_player(player)
            scraped_count += 1
        
        print(f"✅ Enriched {scraped_count} players\n")
        return players

class TeamEvaluator:
    """
    Evaluates team performance from match calendar data.
    Creates statistics for:
    - Entire season
    - Last 3 games
    """
    
    MATCH_STATE_FINISHED = 7
    LAST_N_GAMES = 3
    
    def __init__(
        self, 
        current_week: int, 
        evaluator_file: str = 'config/team_evaluator.json',
        calendar_dir: str = 'data/calendar'
    ):
        """
        Initialize the team evaluator.
        
        Args:
            current_week: Current week number
            evaluator_file: Path to the evaluator JSON file
            calendar_dir: Directory containing week JSON files
        """
        self.current_week = current_week
        self.file_path = Path(evaluator_file)
        self.calendar_path = Path(calendar_dir)
        self.evaluator_data = self._load_evaluator_file()
        self.last_loaded_week = self.evaluator_data.get('last_loaded_week', 1)
        
    def _load_evaluator_file(self) -> Dict:
        """Load or create the evaluator JSON file."""
        if not self.file_path.exists():
            print(f"⚠️  '{self.file_path}' not found. Creating new file.")
            return {'last_loaded_week': 0, 'season_overall': {}, 'last_3_games': {}}
        
        try:
            with open(self.file_path, 'r', encoding='utf-8') as f:
                content = f.read().strip()
                if not content:
                    print(f"⚠️  '{self.file_path}' is empty. Initializing with default data.")
                    return {'last_loaded_week': 0, 'season_overall': {}, 'last_3_games': {}}
                return json.loads(content)
        except json.JSONDecodeError as e:
            print(f"⚠️  Invalid JSON in '{self.file_path}': {e}. Initializing with default data.")
            return {'last_loaded_week': 0, 'season_overall': {}, 'last_3_games': {}}
        except Exception as e:
            print(f"❌ Error reading '{self.file_path}': {e}")
            return {'last_loaded_week': 0, 'season_overall': {}, 'last_3_games': {}}

    def _load_week_matches(self, week: int) -> Optional[list]:
        """Load match data for a specific week."""
        week_files = list(self.calendar_path.glob(f'week_{week}.json'))
        
        if not week_files:
            print(f"⚠️  No file found for week {week} in {self.calendar_path}")
            return None
        
        try:
            with open(week_files[0], 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            print(f"❌ Error loading week {week}: {e}")
            return None

    def _process_match(self, match: Dict, team_stats: Dict[str, TeamStats]) -> None:
        """Process a single match and update team statistics."""
        # Validate match data
        if match.get('matchState') != self.MATCH_STATE_FINISHED:
            return
        
        local_score = match.get('localScore')
        visitor_score = match.get('visitorScore')
        
        if local_score is None or visitor_score is None:
            return
        
        # Get team names
        local_team = match['local']['name']
        visitor_team = match['visitor']['name']
        
        # Initialize teams if not present
        for team in [local_team, visitor_team]:
            if team not in team_stats:
                team_stats[team] = TeamStats()
        
        # Update games played
        team_stats[local_team].games_played += 1
        team_stats[visitor_team].games_played += 1
        
        # Update goals
        team_stats[local_team].goals_scored_home += local_score
        team_stats[local_team].goals_against_home += visitor_score
        team_stats[visitor_team].goals_scored_away += visitor_score
        team_stats[visitor_team].goals_against_away += local_score
        
        # Update win/loss/draw records
        if local_score > visitor_score:  # Home win
            team_stats[local_team].wins_home += 1
            team_stats[visitor_team].losses_away += 1
        elif local_score < visitor_score:  # Away win
            team_stats[local_team].losses_home += 1
            team_stats[visitor_team].wins_away += 1
        else:  # Draw
            team_stats[local_team].draws_home += 1
            team_stats[visitor_team].draws_away += 1

    def _calculate_stats(self, start_week: int, end_week: int) -> Dict[str, Dict]:
        """
        Calculate team statistics for a range of weeks.
        
        Args:
            start_week: Starting week (inclusive)
            end_week: Ending week (exclusive)
        
        Returns:
            Dictionary mapping team names to their statistics
        """
        team_stats: Dict[str, TeamStats] = {}
        
        for week in range(start_week, end_week):
            matches = self._load_week_matches(week)
            if matches is None:
                continue
            
            for match in matches:
                self._process_match(match, team_stats)
        
        # Convert TeamStats objects to dictionaries
        return {team: stats.to_dict() for team, stats in team_stats.items()}

    def update(self) -> bool:
        """
        Update statistics for both season overall and last 3 games.
        
        Returns:
            True if update was successful, False otherwise
        """
        if self.current_week <= self.last_loaded_week:
            print(f"ℹ️  Already up to date (current week: {self.current_week}, "
                  f"last loaded: {self.last_loaded_week})")
            return True
        
        print(f"📊 Updating statistics from week {self.last_loaded_week + 1} "
              f"to {self.current_week}...")
        
        # Calculate season overall stats (incremental update)
        season_stats = self._calculate_stats(self.last_loaded_week + 1, self.current_week + 1)
        
        # Merge with existing season stats
        if 'season_overall' not in self.evaluator_data:
            self.evaluator_data['season_overall'] = {}
        
        for team, stats in season_stats.items():
            if team not in self.evaluator_data['season_overall']:
                self.evaluator_data['season_overall'][team] = stats
            else:
                # Merge stats (add new values to existing)
                existing = self.evaluator_data['season_overall'][team]
                for key, value in stats.items():
                    if isinstance(value, (int, float)):
                        existing[key] = existing.get(key, 0) + value
        
        # Calculate last N games stats (always recalculate)
        start_week = max(1, self.current_week - self.LAST_N_GAMES + 1)
        self.evaluator_data['last_3_games'] = self._calculate_stats(
            start_week, 
            self.current_week + 1
        )
        
        # Update last loaded week
        self.evaluator_data['last_loaded_week'] = self.current_week
        
        # Save to file
        return self._save()

    def _save(self) -> bool:
        """Save evaluator data to JSON file."""
        try:
            with open(self.file_path, 'w', encoding='utf-8') as f:
                json.dump(self.evaluator_data, f, ensure_ascii=False, indent=2)
            print(f"✅ Successfully saved updates to '{self.file_path}'")
            return True
        except Exception as e:
            print(f"❌ Error saving to '{self.file_path}': {e}")
            return False

    def get_team_stats(self, team_name: str, period: str = 'season_overall') -> Optional[Dict]:
        """
        Get statistics for a specific team.
        
        Args:
            team_name: Name of the team
            period: Either 'season_overall' or 'last_3_games'
        
        Returns:
            Team statistics dictionary or None if not found
        """
        return self.evaluator_data.get(period, {}).get(team_name)

    def get_rankings(self, period: str = 'season_overall') -> list:
        """
        Get team rankings sorted by points.
        
        Args:
            period: Either 'season_overall' or 'last_3_games'
        
        Returns:
            List of tuples (team_name, stats) sorted by points
        """
        stats = self.evaluator_data.get(period, {})
        return sorted(
            stats.items(), 
            key=lambda x: (x[1].get('points', 0), x[1].get('goal_difference', 0)),
            reverse=True
        )

# ============================================================================
# DATA LOADING
# ============================================================================

class DataLoader:
    """Loads data from JSON files"""
    
    def __init__(self, data_dir: str = "data"):
        self.data_dir = Path(data_dir)
        self.calendar_dir = self.data_dir / "calendar"
        self.equipos_dir = self.data_dir / "equipos"
        self.market_dir = self.data_dir / "market"
        self.players_dir = self.data_dir / "players"
    
    def load_latest_file(self, directory: Path, prefix: str) -> Optional[Dict]:
        """Load most recent file matching prefix"""
        if not directory.exists():
            print(f"⚠️  Directory not found: {directory}")
            return None
            
        files = sorted(directory.glob(f"{prefix}*.json"), reverse=True)
        
        if not files:
            print(f"⚠️  No files found matching: {prefix} in {directory}")
            return None
            
        latest = files[0]
        with open(latest, 'r', encoding='utf-8') as f:
            return json.load(f)
    
    def load_latest_date(self, directory: Path) -> Optional[str]:
        """Extract date from latest file in directory"""
        if not directory.exists():
            return None
            
        files = sorted(directory.glob("*.json"), reverse=True)
        
        if not files:
            return None
            
        latest = files[0].name
        date_str = latest.split("_")[1].split(".")[0]
        return date_str
    
    def load_calendar(self, week: int) -> List[Fixture]:
        """Load fixtures for specific week"""
        file_path = self.calendar_dir / f"week_{week}.json"
        
        if not file_path.exists():
            print(f"⚠️  Calendar file not found: {file_path}")
            return []
            
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        fixtures = []
        for match in data:
            fixtures.append(Fixture(
                match_id=match['id'],
                match_date=datetime.fromisoformat(match['matchDate']),
                home_team_id=match['local']['id'],
                home_team_name=match['local']['name'],
                away_team_id=match['visitor']['id'],
                away_team_name=match['visitor']['name'],
                home_score=match.get('localScore'),
                away_score=match.get('visitorScore'),
                match_state=match.get('matchState', 0)
            ))
        
        return fixtures
    
    def load_my_team(self, team_name: str = None) -> Optional[Team]:
        """Load user's fantasy team"""
        data = self.load_latest_file(
            self.equipos_dir,
            team_name if team_name else ""
        )
        
        if not data:
            return None
        
        players = self._parse_team_players(data.get('players', []))
        
        return Team(
            team_id=data['id'],
            manager_name=data['manager']['managerName'],
            players=players,
            team_value=data['teamValue'],
            team_points=data['teamPoints'],
            team_money=data.get('teamMoney'),
            position=data['position']
        )
    
    def _parse_team_players(self, players_data: List[Dict]) -> List[Player]:
        """Parse player data from team JSON"""
        players = []
        
        for p in players_data:
            pm = p.get('playerMaster', {})
            last_stats = pm.get('lastStats', [])[-3:]
            
            last_3_points = [s['totalPoints'] for s in last_stats]
            last_3_mins = [
                s.get('stats', {}).get('mins_played', [0])[0]
                for s in last_stats
            ]
            
            players.append(Player(
                id=pm['id'],
                nickname=pm['nickname'],
                position_id=pm['positionId'],
                team_id=pm['team']['id'],
                team_name=pm['team']['name'],
                points=pm.get('points', 0),
                average_points=pm.get('averagePoints', 0.0),
                last_season_points=pm.get('lastSeasonPoints'),
                market_value=pm.get('marketValue', 0),
                player_status=pm.get('playerStatus', 'ok'),
                last_3_weeks=last_3_points,
                minutes_last_3=last_3_mins
            ))
        
        return players
    
    def load_all_players(self) -> List[Player]:
        """Load all players with market and ownership data"""
        data = self.load_latest_file(self.players_dir, "players")
        
        if not data:
            return []
        
        # Create base player objects
        all_players = {}
        for p in data:
            all_players[p['id']] = Player(
                id=p['id'],
                nickname=p['nickname'],
                position_id=int(p['positionId']),
                team_id=p['team']['id'],
                team_name=p['team']['name'],
                points=p.get('points', 0),
                average_points=p.get('averagePoints', 0.0),
                last_season_points=(
                    int(p.get('lastSeasonPoints', 0))
                    if p.get('lastSeasonPoints') else None
                ),
                market_value=int(p.get('marketValue', 0)),
                player_status=p.get('playerStatus', 'ok')
            )
        
        # Enrich with market data
        self._enrich_market_data(all_players)
        
        # Enrich with ownership data
        self._enrich_ownership_data(all_players)
        
        return list(all_players.values())
    
    def _enrich_market_data(self, all_players: Dict[str, Player]) -> None:
        """Add market listing data to players"""
        market_data = self.load_latest_file(self.market_dir, "market")
        
        if not market_data:
            return
        
        for market_entry in market_data:
            pm = market_entry.get('playerMaster', {})
            player_id = pm.get('id')
            
            if player_id not in all_players:
                continue
            
            player = all_players[player_id]
            
            if market_entry.get('discr') != "marketPlayerTeam":
                player.is_on_market = True
                
            player.sale_price = market_entry.get('salePrice')
    
    def _enrich_ownership_data(self, all_players: Dict[str, Player]) -> None:
        """Add ownership and buyout data to players"""
        if not self.equipos_dir.exists():
            return
        
        latest_date = self.load_latest_date(self.equipos_dir)
        
        if not latest_date:
            return
        
        for team_file in self.equipos_dir.glob(f"*{latest_date}.json"):
            with open(team_file, 'r', encoding='utf-8') as f:
                team_data = json.load(f)
            
            manager_name = team_data['manager']['managerName']
            
            for p in team_data.get('players', []):
                pm = p.get('playerMaster', {})
                player_id = pm.get('id')
                
                if player_id not in all_players:
                    continue
                
                player = all_players[player_id]
                player.owned_by = manager_name
                player.buyout_clause = p.get('buyoutClause')
                
                lock_time_str = p.get('buyoutClauseLockedEndTime')
                if lock_time_str:
                    try:
                        player.buyout_locked_until = datetime.fromisoformat(
                            lock_time_str
                        )
                    except Exception:
                        pass
    
    def load_current_week(self) -> int:
        """Load current gameweek number"""
        try:
            file_path = list(self.data_dir.glob("current_week.json"))[0]
        except IndexError:
            print(f"⚠️  {self.data_dir / 'current_week.json'} not found. Defaulting to week 1.")
            return 1
        
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        return data.get('weekNumber', 1)


# ============================================================================
# FIXTURE ANALYSIS
# ============================================================================
class TeamStrengthCalculator:
    """
    Calculates dynamic team strengths from evaluator data.
    Supports multiple data sources: current season, recent form, last season.
    """
    
    # Weights for different metrics
    WEIGHTS = {
        'goals_scored': 0.3,
        'goals_against': 0.3,
        'win_rate': 0.25,
        'points_per_game': 0.15
    }
    
    # Form weights: more recent = more important
    FORM_WEIGHTS = {
        'last_3_games': 0.6,
        'season_overall': 0.3,
        'last_season': 0.1
    }
    
    def __init__(self, evaluator_file: str = 'config/team_evaluator.json'):
        """Initialize with evaluator data."""
        self.evaluator_file = Path(evaluator_file)
        self.evaluator_data = self._load_evaluator_data()
        self._strength_cache = {}
    
    def _load_evaluator_data(self) -> Dict:
        """Load team evaluator data."""
        if not self.evaluator_file.exists():
            print(f"⚠️  Evaluator file '{self.evaluator_file}' not found")
            return {}
        
        try:
            with open(self.evaluator_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            print(f"❌ Error loading evaluator data: {e}")
            return {}
    
    def _normalize_score(self, value: float, min_val: float, max_val: float) -> float:
        """Normalize a value to 1-5 scale."""
        if max_val == min_val:
            return 3.0
        normalized = 1 + 4 * (value - min_val) / (max_val - min_val)
        return max(1.0, min(5.0, normalized))
    
    def _convert_last_season_ranking(self, ranking: int) -> Dict[str, float]:
        """
        Convert last season ranking to strength estimate.
        Lower ranking = stronger team.
        
        Args:
            ranking: Team's final position (1-20)
        
        Returns:
            Estimated attack/defense ratings
        """
        # Convert ranking (1-20) to strength (5.0-2.5)
        # 1st place = 5.0, 20th place = 2.5
        strength = 5.5 - (ranking * 0.15)
        strength = max(2.5, min(5.0, strength))
        
        return {'attack': round(strength, 2), 'defense': round(strength, 2)}
    
    def _calculate_attack_defense(
        self,
        team_stats: Dict,
        league_stats: Dict
    ) -> Tuple[float, float]:
        """
        Calculate attack and defense ratings for a team.
        
        Args:
            team_stats: Individual team statistics
            league_stats: All teams' statistics for normalization
        
        Returns:
            Tuple of (attack_rating, defense_rating) on 1-5 scale
        """
        # Check if team_stats is a dict with the required structure
        if not isinstance(team_stats, dict) or team_stats.get('games_played', 0) == 0:
            return 3.0, 3.0
        
        games = team_stats['games_played']
        
        # Calculate per-game metrics
        goals_scored_pg = team_stats['total_goals_scored'] / games
        goals_against_pg = team_stats['total_goals_against'] / games
        win_rate = team_stats['total_wins'] / games
        points_pg = team_stats['points'] / games
        
        # Get league ranges for normalization
        all_goals_scored = [s['total_goals_scored'] / s['games_played'] 
                           for s in league_stats.values() 
                           if isinstance(s, dict) and s.get('games_played', 0) > 0]
        all_goals_against = [s['total_goals_against'] / s['games_played'] 
                            for s in league_stats.values() 
                            if isinstance(s, dict) and s.get('games_played', 0) > 0]
        all_win_rates = [s['total_wins'] / s['games_played'] 
                        for s in league_stats.values() 
                        if isinstance(s, dict) and s.get('games_played', 0) > 0]
        all_points_pg = [s['points'] / s['games_played'] 
                        for s in league_stats.values() 
                        if isinstance(s, dict) and s.get('games_played', 0) > 0]
        
        # Normalize individual components
        attack_goals = self._normalize_score(
            goals_scored_pg, 
            min(all_goals_scored), 
            max(all_goals_scored)
        )
        attack_wins = self._normalize_score(
            win_rate,
            min(all_win_rates),
            max(all_win_rates)
        )
        attack_points = self._normalize_score(
            points_pg,
            min(all_points_pg),
            max(all_points_pg)
        )
        
        # Defense: lower goals against = better (invert scale)
        defense_goals = self._normalize_score(
            max(all_goals_against) + min(all_goals_against) - goals_against_pg,
            min(all_goals_against),
            max(all_goals_against)
        )
        defense_wins = attack_wins  # Wins contribute to both
        defense_points = attack_points  # Points contribute to both
        
        # Weighted combination
        attack = (
            attack_goals * self.WEIGHTS['goals_scored'] +
            attack_wins * self.WEIGHTS['win_rate'] +
            attack_points * self.WEIGHTS['points_per_game']
        ) / (self.WEIGHTS['goals_scored'] + 
             self.WEIGHTS['win_rate'] + 
             self.WEIGHTS['points_per_game'])
        
        defense = (
            defense_goals * self.WEIGHTS['goals_against'] +
            defense_wins * self.WEIGHTS['win_rate'] +
            defense_points * self.WEIGHTS['points_per_game']
        ) / (self.WEIGHTS['goals_against'] + 
             self.WEIGHTS['win_rate'] + 
             self.WEIGHTS['points_per_game'])
        
        return round(attack, 2), round(defense, 2)
    
    def calculate_team_strength(
        self,
        team_name: str,
        use_form: bool = True
    ) -> Dict[str, float]:
        """
        Calculate dynamic team strength ratings.
        
        Args:
            team_name: Name of the team
            use_form: If True, weight recent form more heavily
        
        Returns:
            Dictionary with 'attack' and 'defense' ratings (1-5 scale)
        """
        cache_key = f"{team_name}_{use_form}"
        if cache_key in self._strength_cache:
            return self._strength_cache[cache_key]
        
        if not self.evaluator_data:
            return {'attack': 3.0, 'defense': 3.0}
        
        if use_form:
            # Combine multiple data sources with form weights
            strengths = []
            total_weight = 0
            
            for period, weight in self.FORM_WEIGHTS.items():
                period_data = self.evaluator_data.get(period, {})
                if team_name in period_data:
                    team_data = period_data[team_name]
                    
                    # Handle last_season data (just rankings as integers)
                    if period == 'last_season' and isinstance(team_data, int):
                        strength_dict = self._convert_last_season_ranking(team_data)
                        attack = strength_dict['attack']
                        defense = strength_dict['defense']
                    else:
                        # Normal calculation for season_overall and last_3_games
                        attack, defense = self._calculate_attack_defense(
                            team_data,
                            period_data
                        )
                    
                    strengths.append((attack, defense, weight))
                    total_weight += weight
            
            if not strengths:
                result = {'attack': 3.0, 'defense': 3.0}
            else:
                avg_attack = sum(a * w for a, d, w in strengths) / total_weight
                avg_defense = sum(d * w for a, d, w in strengths) / total_weight
                result = {
                    'attack': round(avg_attack, 2),
                    'defense': round(avg_defense, 2)
                }
        else:
            # Use only season overall data
            season_data = self.evaluator_data.get('season_overall', {})
            if team_name in season_data:
                attack, defense = self._calculate_attack_defense(
                    season_data[team_name],
                    season_data
                )
                result = {'attack': attack, 'defense': defense}
            else:
                result = {'attack': 3.0, 'defense': 3.0}
        
        self._strength_cache[cache_key] = result
        return result
    
    def get_all_team_strengths(self, use_form: bool = True) -> Dict[str, Dict]:
        """Get strength ratings for all teams."""
        season_data = self.evaluator_data.get('season_overall', {})
        return {
            team: self.calculate_team_strength(team, use_form)
            for team in season_data.keys()
        }
    
    def clear_cache(self):
        """Clear the strength calculation cache."""
        self._strength_cache.clear()

class FixtureAnalyzer:
    """
    Analyzes fixture difficulty and schedules using dynamic team strengths.
    """
    
    def __init__(
        self,
        fixtures: List[Fixture],
        strength_calculator: Optional[TeamStrengthCalculator] = None,
        evaluator_file: str = 'config/team_evaluator.json'
    ):
        """
        Initialize fixture analyzer.
        
        Args:
            fixtures: List of Fixture objects
            strength_calculator: Optional pre-initialized calculator
            evaluator_file: Path to evaluator JSON file
        """
        self.fixtures = fixtures
        self.strength_calculator = strength_calculator or TeamStrengthCalculator(evaluator_file)
        self.default_strength = {'attack': 3.0, 'defense': 3.0}
    
    def get_team_strength(self, team_name: str) -> Dict[str, float]:
        """Get strength ratings for a team."""
        return self.strength_calculator.calculate_team_strength(team_name)
    
    def get_fixture_difficulty(
        self,
        team_id: str,
        team_name: str,
        next_n_weeks: int = 3
    ) -> List[Dict]:
        """
        Calculate fixture difficulty for a team.
        
        Args:
            team_id: Team ID
            team_name: Team name
            next_n_weeks: Number of upcoming fixtures to analyze
        
        Returns:
            List of fixture dictionaries with difficulty ratings
        """
        team_fixtures = []
        
        for fixture in self.fixtures:
            if fixture.home_team_id == team_id:
                opponent = fixture.away_team_name
                is_home = True
            elif fixture.away_team_id == team_id:
                opponent = fixture.home_team_name
                is_home = False
            else:
                continue
            
            difficulty = self._calculate_match_difficulty(opponent, is_home)
            
            team_fixtures.append({
                'opponent': opponent,
                'is_home': is_home,
                'difficulty': difficulty,
                'difficulty_rating': self._difficulty_label(difficulty)
            })
            
            if len(team_fixtures) >= next_n_weeks:
                break
        
        return team_fixtures
    
    def _calculate_match_difficulty(
        self,
        opponent: str,
        is_home: bool
    ) -> float:
        """
        Calculate difficulty score for a match (1-5 scale).
        
        Args:
            opponent: Opponent team name
            is_home: Whether playing at home
        
        Returns:
            Difficulty score (1=easiest, 5=hardest)
        """
        opp_strength = self.get_team_strength(opponent)
        
        # Average of attack and defense strength
        avg_opponent_strength = (
            opp_strength['attack'] + opp_strength['defense']
        ) / 2
        
        # Opponent strength directly translates to difficulty
        difficulty = avg_opponent_strength
        
        # Home advantage adjustment
        if is_home:
            difficulty -= 0.5  # Home games are easier
        else:
            difficulty += 0.3  # Away games are harder
        
        # Clamp to 1-5 range
        return max(1.0, min(5.0, round(difficulty, 2)))
    
    def _difficulty_label(self, difficulty: float) -> str:
        """Convert difficulty score to label."""
        if difficulty < 2.0:
            return "Very Easy"
        elif difficulty < 3.0:
            return "Easy"
        elif difficulty < 3.5:
            return "Medium"
        elif difficulty < 4.5:
            return "Hard"
        else:
            return "Very Hard"
    
    def calculate_fixture_score(
        self,
        player: Player,
        next_weeks: int = 3
    ) -> float:
        """
        Calculate weighted fixture difficulty score (2-10 scale).
        
        Args:
            player: Player object with team info
            next_weeks: Number of weeks to analyze
        
        Returns:
            Weighted average difficulty score (2-10 scale)
        """
        fixtures = self.get_fixture_difficulty(
            player.team_id,
            player.team_name,
            next_weeks
        )
        
        if not fixtures:
            return 5.0
        
        # Weight more recent fixtures higher
        weights = [1.0, 0.8, 0.6, 0.4, 0.2][:len(fixtures)]
        scores = [f['difficulty'] * 2 for f in fixtures]  # Scale to 2-10
        
        weighted_avg = sum(
            s * w for s, w in zip(scores, weights)
        ) / sum(weights)
        
        return round(weighted_avg, 2)
    
    def compare_fixtures(
        self,
        team_ids: List[Tuple[str, str]],
        next_weeks: int = 3
    ) -> List[Dict]:
        """
        Compare fixture difficulty across multiple teams.
        
        Args:
            team_ids: List of (team_id, team_name) tuples
            next_weeks: Number of weeks to analyze
        
        Returns:
            List of team fixture analyses sorted by difficulty (easiest first)
        """
        comparisons = []
        
        for team_id, team_name in team_ids:
            fixtures = self.get_fixture_difficulty(team_id, team_name, next_weeks)
            avg_difficulty = sum(f['difficulty'] for f in fixtures) / len(fixtures) if fixtures else 3.0
            
            comparisons.append({
                'team_id': team_id,
                'team_name': team_name,
                'fixtures': fixtures,
                'avg_difficulty': round(avg_difficulty, 2),
                'total_home': sum(1 for f in fixtures if f['is_home']),
                'total_away': sum(1 for f in fixtures if not f['is_home'])
            })
        
        return sorted(comparisons, key=lambda x: x['avg_difficulty'])
    
    def get_best_fixture_runs(
        self,
        min_weeks: int = 3,
        max_difficulty: float = 3.0
    ) -> List[Dict]:
        """
        Find teams with favorable fixture runs.
        
        Args:
            min_weeks: Minimum number of weeks in a run
            max_difficulty: Maximum average difficulty threshold
        
        Returns:
            List of teams with good fixture runs
        """
        # Get all unique teams from fixtures
        teams = {}
        for fixture in self.fixtures:
            teams[fixture.home_team_id] = fixture.home_team_name
            teams[fixture.away_team_id] = fixture.away_team_name
        
        good_runs = []
        
        for team_id, team_name in teams.items():
            fixtures = self.get_fixture_difficulty(team_id, team_name, min_weeks)
            
            if len(fixtures) >= min_weeks:
                avg_difficulty = sum(f['difficulty'] for f in fixtures) / len(fixtures)
                
                if avg_difficulty <= max_difficulty:
                    good_runs.append({
                        'team_id': team_id,
                        'team_name': team_name,
                        'avg_difficulty': round(avg_difficulty, 2),
                        'fixtures': fixtures
                    })
        
        return sorted(good_runs, key=lambda x: x['avg_difficulty'])

# ============================================================================
# PLAYER EVALUATION
# ============================================================================

class PlayerEvaluator:
    """Evaluates players using multiple metrics"""
    
    # Score weights (total = 100)
    WEIGHTS = {
        'form': 15,
        'fixtures': 20,
        'ppg': 15,
        'value': 10,
        'jerarquia': 15,
        'probability': 10,
        'injury': 5
    }
    
    def __init__(self, fixture_analyzer: FixtureAnalyzer):
        self.fixture_analyzer = fixture_analyzer
    
    def evaluate_player(self, player: Player) -> Dict:
        """Comprehensive player evaluation"""
        # Calculate individual scores
        form_score = self._calculate_form_score(player)
        fixture_score = self._calculate_fixture_score(player)
        ppg_score = self._calculate_ppg_score(player)
        value_score = self._calculate_value_score(player)
        jerarquia_score = self._calculate_jerarquia_score(player)
        probability_score = self._calculate_probability_score(player)
        injury_score = self._calculate_injury_score(player)
        
        # Calculate total score
        total_score = (
            form_score + fixture_score + ppg_score + value_score +
            jerarquia_score + probability_score + injury_score
        )
        
        # Apply penalties
        total_score = self._apply_penalties(player, total_score)
        
        return {
            'total_score': total_score,
            'form': player.form_last_3(),
            'form_score': form_score,
            'fixtures': self.fixture_analyzer.calculate_fixture_score(player),
            'fixture_score': fixture_score,
            'ppg': player.points_per_game(),
            'ppg_score': ppg_score,
            'value': player.points_per_game() / max(player.price_in_millions(), 0.1),
            'value_score': value_score,
            'jerarquia_score': jerarquia_score,
            'probability_score': probability_score,
            'injury_score': injury_score,
            'minutes_reliability': player.minutes_reliability(),
            'is_available': player.is_available(),
            'scraped_jerarquia': (
                player.scraped_data.jerarquia if player.scraped_data else None
            ),
            'scraped_probability': (
                player.scraped_data.play_probability if player.scraped_data else None
            ),
            'scraped_form_arrow': (
                player.scraped_data.form_arrow if player.scraped_data else None
            ),
            'scraped_injury_risk': (
                player.scraped_data.injury_risk if player.scraped_data else None
            ),
        }
    
    def _calculate_form_score(self, player: Player) -> float:
        """Calculate form score (max 15 points)"""
        form_raw = player.form_last_3()
        form_score = min(form_raw / 10.0, 1.0) * self.WEIGHTS['form']
        
        # Add web-scraped form data
        if player.scraped_data and player.scraped_data.form_arrow:
            form_score += player.scraped_data.get_form_score() * 10
        else:
            form_score += 5
        
        return form_score
    
    def _calculate_fixture_score(self, player: Player) -> float:
        """Calculate fixture difficulty score (max 20 points)"""
        fixture_raw = self.fixture_analyzer.calculate_fixture_score(player)
        
        # Invert: easier fixtures = higher score
        fixture_score = (10 - (fixture_raw - 2)) / 8 * self.WEIGHTS['fixtures']
        
        return fixture_score
    
    def _calculate_ppg_score(self, player: Player) -> float:
        """Calculate points per game score (max 15 points)"""
        ppg_raw = player.points_per_game()
        return min(ppg_raw / 10.0, 1.0) * self.WEIGHTS['ppg']
    
    def _calculate_value_score(self, player: Player) -> float:
        """Calculate value for money score (max 10 points)"""
        value_raw = player.points_per_game() / max(player.price_in_millions(), 0.1)
        return min(value_raw / 2.0, 1.0) * self.WEIGHTS['value']
    
    def _calculate_jerarquia_score(self, player: Player) -> float:
        """Calculate team hierarchy score (max 15 points)"""
        if player.scraped_data and player.scraped_data.jerarquia:
            return player.scraped_data.get_jerarquia_score() * self.WEIGHTS['jerarquia']
        return self.WEIGHTS['jerarquia'] / 2  # Default to 50%
    
    def _calculate_probability_score(self, player: Player) -> float:
        """Calculate play probability score (max 10 points)"""
        if player.scraped_data and player.scraped_data.play_probability:
            return player.scraped_data.play_probability * self.WEIGHTS['probability']
        return self.WEIGHTS['probability'] * 0.7  # Default to 70%
    
    def _calculate_injury_score(self, player: Player) -> float:
        """Calculate injury risk score (max 5 points)"""
        if player.scraped_data and player.scraped_data.injury_risk:
            return player.scraped_data.get_injury_risk_score() * self.WEIGHTS['injury']
        return self.WEIGHTS['injury'] * 0.7  # Default to 70%
    
    def _apply_penalties(self, player: Player, score: float) -> float:
        """Apply penalties for low minutes or injury"""
        if player.minutes_reliability() < 0.6:
            score *= 0.7
        
        if player.player_status != "ok":
            score *= 0.5
        
        return score
    
    def find_best_transfers(
        self,
        current_team: Team,
        available_players: List[Player],
        budget: float,
        max_suggestions: int = 5
    ) -> List[Dict]:
        """Find optimal transfer suggestions"""
        current_time = datetime.now(timezone.utc)
        
        # Evaluate current squad
        current_scores = {}
        for player in current_team.players:
            if player.position_id == 5:  # Skip coaches
                continue
            current_scores[player.id] = self.evaluate_player(player)
        
        # Find transfer opportunities
        transfer_suggestions = []
        
        for current_player in current_team.players:
            if current_player.position_id == 5:
                continue
            
            current_eval = current_scores[current_player.id]
            
            # Filter candidates
            candidates = [
                p for p in available_players
                if (p.id not in [cp.id for cp in current_team.players] and
                    p.is_available() and
                    p.is_transferable(current_time))
            ]
            
            # Evaluate each candidate
            for candidate in candidates:
                acquisition_cost = candidate.get_acquisition_cost()
                net_cost = acquisition_cost - current_player.price_in_millions()
                
                if net_cost > budget:
                    continue
                
                candidate_eval = self.evaluate_player(candidate)
                score_improvement = (
                    candidate_eval['total_score'] - current_eval['total_score']
                )
                
                # Only suggest if significant improvement
                if score_improvement > 3:
                    acq_type = self._get_acquisition_type(
                        candidate, current_time
                    )
                    
                    transfer_suggestions.append({
                        'player_out': current_player,
                        'player_out_score': current_eval['total_score'],
                        'player_out_eval': current_eval,
                        'player_in': candidate,
                        'player_in_score': candidate_eval['total_score'],
                        'player_in_eval': candidate_eval,
                        'improvement': score_improvement,
                        'acquisition_cost': acquisition_cost,
                        'net_cost': net_cost,
                        'value_ratio': score_improvement / max(abs(net_cost), 0.1),
                        'acquisition_type': acq_type
                    })
        
        # Sort by value ratio
        transfer_suggestions.sort(key=lambda x: x['value_ratio'], reverse=True)
        
        return transfer_suggestions[:max_suggestions]
    
    def _get_acquisition_type(
        self,
        player: Player,
        current_time: datetime
    ) -> str:
        """Determine how a player can be acquired"""
        if player.is_on_market:
            return "Market"
        
        if player.buyout_locked_until and player.buyout_locked_until < current_time:
            return f"Buyout (from {player.owned_by})"
        
        return "Unknown"


# ============================================================================
# MAIN AGENT
# ============================================================================

class FantasyAgent:
    """Main agent for fantasy team analysis and recommendations"""
    
    def __init__(self, data_dir: str = "data"):
        try:
            data_downloader()
        except:
            print("Failed to refresh data")
        self.loader = DataLoader(data_dir)
        self.scraper_manager = ScraperManager(
            cache_dir="scrapper_cache",
            mapping_path="config/name_mapping.json"
        )
        self.current_week = -1
        self.my_team = None
        self.all_players = []
        self.fixture_analyzer = None
        self.team_evaluator = None # Renamed from 'evaluator' to avoid clash
        self.player_evaluator = None # Renamed from 'evaluator'
    
    def initialize(
        self,
        team_name: str = None,
        enrich_current_team: bool = True
    ) -> Optional[Dict]:
        """Initialize agent with data from files"""
        print("Initializing Fantasy Agent...")
        print("=" * 60)
        
        try:
            self.current_week = self.loader.load_current_week()
            self.my_team = self.loader.load_my_team(team_name)
        except Exception as e:
            print(f"❌ CRITICAL ERROR: {e}")
            print(f"   Ensure JSON files exist in: {self.loader.equipos_dir} /players, /market")
            print("   Run download_pipeline.py to fetch data.")
            return None
        
        if not self.my_team:
            print(f"❌ Could not load team! Check '{self.loader.equipos_dir}' directory.")
            return None
        
        if enrich_current_team:
            for player in self.my_team.players:
                self.scraper_manager.enrich_player(player)
        
        self.all_players = self.loader.load_all_players()
        fixtures = self.loader.load_calendar(self.current_week)
        
        # Initialize and update team evaluator
        self.team_evaluator = TeamEvaluator(
            self.current_week,
            evaluator_file='config/team_evaluator.json',
            calendar_dir=self.loader.calendar_dir
        )
        self.team_evaluator.update()
        
        # Initialize fixture analyzer
        self.fixture_analyzer = FixtureAnalyzer(
            fixtures,
            evaluator_file='config/team_evaluator.json'
        )
        
        # Initialize player evaluator
        self.player_evaluator = PlayerEvaluator(self.fixture_analyzer)
        
        print("✅ Agent ready!")
        print("=" * 60)
        
        return {
            "name": self.my_team.manager_name,
            "value": f"€{self.my_team.total_value_millions():.1f}M",
            "budget": f"€{self.my_team.budget_millions():.1f}M"
        }
    
    def analyze_current_team(self) -> List[Dict]:
        """Analyze current team performance"""
        if not self.my_team or not self.player_evaluator:
            print("❌ No team loaded or evaluator not initialized")
            return []
        
        print("\n📊 Analyzing Team...")
        team_analysis = []
        
        for player in self.my_team.players:
            if player.position_id == 5:  # Skip coaches
                continue
            
            eval_result = self.player_evaluator.evaluate_player(player)
            
            # Extract scraped data safely
            scraped = player.scraped_data
            web_jerarquia = scraped.jerarquia if scraped else None
            web_prob = scraped.play_probability if scraped else None
            web_form = scraped.form_arrow if scraped else None
            web_risk = scraped.injury_risk if scraped else None
            
            player_dict = {
                "Name": player.nickname,
                "Pos": Player.POSITION_NAMES.get(player.position_id, '?'),
                "Team": player.team_name,
                "Score": round(eval_result['total_score'], 1),
                "Form (L3)": round(eval_result['form'], 1),
                "Season": round(eval_result['ppg'], 1),
                "Fixtures": round(eval_result['fixtures'], 1),
                "Price": f"€{player.price_in_millions():.1f}M",
                "Status": player.player_status.upper(),
                "Jerarquía": f"{web_jerarquia}/6" if web_jerarquia else "N/A",
                "Play Prob": f"{web_prob*100:.0f}%" if web_prob is not None else "N/A",
                "Form Arrow": f"{'🔥' * web_form}" if web_form else "N/A",
                "Injury Risk": web_risk if web_risk else "N/A"
            }
            team_analysis.append(player_dict)
        
        return team_analysis
    
    def suggest_transfers(
        self,
        max_suggestions: int = 5,
        enrich_candidates: bool = True
    ) -> List[Dict]:
        """Generate transfer suggestions"""
        if not self.my_team or not self.player_evaluator:
            print("❌ Agent not initialized")
            return []
        
        budget = self.my_team.budget_millions()
        print(f"\n💡 Finding Transfers (Budget: €{budget:.1f}M)...")
        
        # Get transferable players
        transferable = [
            p for p in self.all_players
            if p.is_transferable()
        ]
        
        # Pre-filter top candidates by position
        top_candidates = self._filter_top_candidates(transferable)
        print(f"   - Filtered to {len(top_candidates)} top candidates")
        
        # Enrich candidate data
        if enrich_candidates and top_candidates:
            self.scraper_manager.enrich_players_batch(top_candidates)
        
        # Get transfer suggestions
        suggestions = self.player_evaluator.find_best_transfers(
            self.my_team,
            self.all_players,
            budget,
            max_suggestions
        )
        
        if not suggestions:
            print("\n✅ No beneficial transfers found. Team looks solid!")
            return []
        
        # Format for dashboard
        return self._format_transfer_suggestions(suggestions, budget)
    
    def _filter_top_candidates(
        self,
        players: List[Player],
        top_n_per_position: int = 15
    ) -> List[Player]:
        """Filter top players by position"""
        candidates_by_position = {1: [], 2: [], 3: [], 4: []}
        
        for player in players:
            if player.position_id in candidates_by_position:
                candidates_by_position[player.position_id].append(player)
        
        # Sort by average points and take top N
        for pos in candidates_by_position:
            candidates_by_position[pos].sort(
                key=lambda p: p.average_points,
                reverse=True
            )
            candidates_by_position[pos] = (
                candidates_by_position[pos][:top_n_per_position]
            )
        
        # Flatten list
        return [
            p for pos_candidates in candidates_by_position.values()
            for p in pos_candidates
        ]
    
    def _format_transfer_suggestions(
        self,
        suggestions: List[Dict],
        budget: float
    ) -> List[Dict]:
        """Format transfer suggestions for dashboard"""
        formatted = []
        
        for transfer in suggestions:
            in_eval = transfer['player_in_eval']
            out_eval = transfer['player_out_eval']
            
            # Format incoming player data
            in_form_arrow = in_eval.get('scraped_form_arrow')
            in_form_str = (
                f"{'🔥' * in_form_arrow} ({in_form_arrow}/5)"
                if in_form_arrow else "N/A"
            )
            
            in_risk = in_eval.get('scraped_injury_risk')
            risk_emoji = {"Bajo": "✅", "Medio": "⚠️", "Alto": "🚨"}
            in_risk_str = (
                f"{in_risk} {risk_emoji.get(in_risk, '❓')}"
                if in_risk else "N/A"
            )
            
            in_prob = in_eval.get('scraped_probability')
            prob_emoji = (
                '✅' if in_prob and in_prob > 0.7
                else '⚠️' if in_prob and in_prob > 0.4
                else '🚨'
            )
            in_prob_str = (
                f"{in_prob*100:.0f}% {prob_emoji}"
                if in_prob is not None else "N/A"
            )
            
            in_jerarquia = in_eval.get('scraped_jerarquia')
            in_jerarquia_str = (
                f"{in_jerarquia}/6 {'⭐' if in_jerarquia and in_jerarquia <= 2 else ''}"
                if in_jerarquia else "N/A"
            )
            
            formatted.append({
                "improvement": f"{transfer['improvement']:.1f}",
                "out_name": transfer['player_out'].nickname,
                "out_team": transfer['player_out'].team_name,
                "out_score": f"{transfer['player_out_score']:.1f}/100",
                "out_price": f"€{transfer['player_out'].price_in_millions():.1f}M",
                "out_jerarquia": f"{out_eval.get('scraped_jerarquia', 'N/A')}/6",
                "out_prob": f"{out_eval.get('scraped_probability', 0)*100:.0f}%",
                "in_name": transfer['player_in'].nickname,
                "in_team": transfer['player_in'].team_name,
                "in_score": f"{transfer['player_in_score']:.1f}/100",
                "in_price": f"€{transfer['acquisition_cost']:.1f}M",
                "in_source": transfer['acquisition_type'],
                "in_jerarquia": in_jerarquia_str,
                "in_prob": in_prob_str,
                "in_form": in_form_str,
                "in_risk": in_risk_str,
                "net_cost": f"€{transfer['net_cost']:.1f}M",
                "value_ratio": f"{transfer['value_ratio']:.2f}",
                "remaining_budget": f"€{budget - transfer['net_cost']:.1f}M"
            })
        
        return formatted
    
    def show_upcoming_fixtures(self) -> Dict[str, List[str]]:
        """Show upcoming fixtures for teams in squad"""
        if not self.my_team or not self.fixture_analyzer:
            print("❌ Agent not initialized")
            return {}
        
        print(f"\n📅 Analyzing Fixtures...")
        fixture_data = {}
        teams_shown = set()
        
        for player in self.my_team.players:
            if player.team_id in teams_shown or player.position_id == 5:
                continue
            
            fixtures = self.fixture_analyzer.get_fixture_difficulty(
                player.team_id,
                player.team_name,
                3
            )
            
            if fixtures:
                fixture_strings = []
                for fix in fixtures:
                    home = "🏠" if fix['is_home'] else "✈️ "
                    difficulty_stars = "★" * int(fix['difficulty'])
                    
                    fix_str = (
                        f"{home} vs {fix['opponent']} - "
                        f"{difficulty_stars} ({fix['difficulty']:.1f}/5)"
                    )
                    fixture_strings.append(fix_str)
                
                fixture_data[player.team_name] = fixture_strings
            
            teams_shown.add(player.team_id)
        
        return fixture_data


# ============================================================================
# MAIN EXECUTION
# ============================================================================

def main():
    """Main execution function"""
    # Updated to point to the new data directory
    agent = FantasyAgent(data_dir="data")
    
    team_summary = agent.initialize(
        team_name="svendsinio",
        enrich_current_team=True
    )
    
    if not team_summary:
        print("Failed to initialize agent")
        return
    
    print("\n" + "=" * 60)
    print("TEAM SUMMARY")
    print("=" * 60)
    for key, value in team_summary.items():
        print(f"{key.title()}: {value}")
    
    # Team Analysis
    team_analysis = agent.analyze_current_team()
    print("\n" + "=" * 60)
    print("TEAM ANALYSIS")
    print("=" * 60)
    
    if team_analysis:
        # Print header
        headers = list(team_analysis[0].keys())
        header_row = " | ".join(f"{h:12}" for h in headers[:5])
        print(header_row)
        print("-" * len(header_row))
        
        # Print rows
        for player in sorted(team_analysis, key=lambda x: x['Score'], reverse=True):
            row = " | ".join(f"{str(player[h]):12}" for h in headers[:5])
            print(row)
    
    # Fixtures
    fixtures = agent.show_upcoming_fixtures()
    print("\n" + "=" * 60)
    print("UPCOMING FIXTURES")
    print("=" * 60)
    
    for team, fix_list in fixtures.items():
        print(f"\n{team}:")
        for fixture in fix_list:
            print(f"  {fixture}")
    
    # Transfer Suggestions
    transfers = agent.suggest_transfers(
        max_suggestions=5,
        enrich_candidates=True
    )
    print("\n" + "=" * 60)
    print("TRANSFER SUGGESTIONS")
    print("=" * 60)
    
    if transfers:
        for i, transfer in enumerate(transfers, 1):
            print(f"\n{i}. Improvement: {transfer['improvement']} points")
            print(f"   OUT: {transfer['out_name']} ({transfer['out_team']}) - {transfer['out_score']}")
            print(f"   IN:  {transfer['in_name']} ({transfer['in_team']}) - {transfer['in_score']}")
            print(f"   Cost: {transfer['net_cost']} ({transfer['in_source']})")
            print(f"   Value Ratio: {transfer['value_ratio']}")
    
    print("\n" + "=" * 60)
    print("✅ Analysis complete!")
    print("=" * 60)


if __name__ == "__main__":
    main()