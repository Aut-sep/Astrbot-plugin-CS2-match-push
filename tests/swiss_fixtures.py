"""Deterministic fictional Major data, with valid 16-team Swiss progression."""
from collections import defaultdict
from copy import deepcopy
from datetime import datetime, timedelta, timezone

NAMES = ['NAVI','Vitality','Spirit','MOUZ','Falcons','FaZe','G2','The MongolZ',
         'FURIA','Liquid','Aurora','HEROIC','TYLOO','paiN','GamerLegion','VP']
TEAMS = [{'id':index+1,'name':name} for index,name in enumerate(NAMES)]


def pair(teams,seen):
    if not teams:return []
    left=teams[0]
    for index in range(1,len(teams)):
        right=teams[index]
        if frozenset((left['id'],right['id'])) in seen:continue
        tail=pair(teams[1:index]+teams[index+1:],seen)
        if tail is not None:return [(left,right),*tail]
    return None


def swiss_matches(stage=1):
    records={team['id']:(0,0) for team in TEAMS}
    seen=set();rows=[]
    origin=datetime(2026,11,10,10,tzinfo=timezone.utc)+timedelta(days=(stage-1)*4)
    mid=stage*1000
    for number in range(1,6):
        buckets=defaultdict(list)
        for team in TEAMS:
            wins,losses=records[team['id']]
            if wins<3 and losses<3:buckets[(wins,losses)].append(team)
        for (wins,losses),teams in sorted(buckets.items(),reverse=True):
            pairs=pair(teams,seen)
            assert pairs is not None
            for index,(left,right) in enumerate(pairs):
                mid+=1;seen.add(frozenset((left['id'],right['id'])))
                # Alternate results deterministically, rather than simulate a real event.
                winner=left if (index+number+stage)%3 else right
                loser=right if winner is left else left
                bo=3 if wins==2 or losses==2 else 1
                results=[{'team_id':team['id'],'score':(2 if bo==3 else 1) if team is winner else (1 if bo==3 else 0)} for team in (left,right)]
                start=origin+timedelta(hours=(number-1)*12+index)
                rows.append({'id':mid,'name':f'Swiss Round {number} ({wins}-{losses}): {left["name"]} vs {right["name"]}',
                    'tournament_id':stage,'tournament':{'id':stage,'name':f'Stage {stage} Swiss','tier':'s'},
                    'league':{'name':'MAJOR DEMO 2026'},'serie':{'id':99,'name':'Virtual Major'},
                    'status':'finished','scheduled_at':start.isoformat(),'begin_at':start.isoformat(),
                    'opponents':[{'opponent':deepcopy(left)},{'opponent':deepcopy(right)}],
                    'number_of_games':bo,'winner':deepcopy(winner),'results':results,
                    'games':[{'position':1,'winner':{'id':winner['id']},'status':'finished'}]})
                records[winner['id']]=(wins+1,losses)
                records[loser['id']]=(wins,losses+1)
    assert len(rows)==33 and sum(wins==3 for wins,losses in records.values())==8
    return rows


def knockout_matches():
    previous=[];rows=[];mid=4000
    for round_index,name in enumerate(('Quarterfinal','Semifinal','Grand Final')):
        teams=TEAMS[:8] if round_index==0 else [row['winner'] for row in previous]
        current=[]
        for index in range(0,len(teams),2):
            mid+=1;left,right=teams[index:index+2];winner=left
            row={'id':mid,'name':name+f' {index//2+1}: {left["name"]} vs {right["name"]}',
                 'status':'finished','tournament_id':4,'tournament':{'id':4,'name':'Playoffs','tier':'s'},
                 'league':{'name':'MAJOR DEMO 2026'},'number_of_games':3,
                 'scheduled_at':f'2026-11-{23+round_index:02d}T10:00:00Z',
                 'opponents':[{'opponent':deepcopy(left)},{'opponent':deepcopy(right)}],
                 'winner':deepcopy(winner),'results':[{'team_id':left['id'],'score':2},{'team_id':right['id'],'score':1}],
                 'previous_matches':[] if not previous else [{'match_id':previous[index]['id'],'type':'winner'},
                                                            {'match_id':previous[index+1]['id'],'type':'winner'}]}
            current.append(row);rows.append(row)
        previous=current
    return rows
