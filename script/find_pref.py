import requests
import re
import os

# Read GitHub token from the '.git_token' file in the same directory
with open('.git_token', 'r') as token_file:
    TOKEN = token_file.read().strip()

BASE_OWNER = 'ChampSim'
BASE_REPO = 'ChampSim'
DIRECTORY = 'prefetcher'
PATTERN = re.compile(r'stream|berti|bingo', re.IGNORECASE)

headers = {'Authorization': f'token {TOKEN}'}

# Retrieve forks of the base repository
def get_forks(owner, repo):
    url = f'https://api.github.com/repos/{owner}/{repo}/forks?per_page=100'
    response = requests.get(url, headers=headers)
    response.raise_for_status()
    return response.json()

# Retrieve branches of a given fork
def get_branches(owner, repo):
    url = f'https://api.github.com/repos/{owner}/{repo}/branches'
    response = requests.get(url, headers=headers)
    if response.status_code != 200:
        return []
    return [branch['name'] for branch in response.json()]

# Recursively check directories for matching patterns and record matched patterns
def check_directory(owner, repo, branch, directory, matched_patterns):
    url = f'https://api.github.com/repos/{owner}/{repo}/contents/{directory}?ref={branch}'
    response = requests.get(url, headers=headers)
    if response.status_code != 200:
        return

    contents = response.json()
    for item in contents:
        match = PATTERN.findall(item['name'])
        if match:
            matched_patterns.update(m.lower() for m in match)
        if item['type'] == 'dir':
            check_directory(owner, repo, branch, f"{directory}/{item['name']}", matched_patterns)

forks = get_forks(BASE_OWNER, BASE_REPO)

# Dictionary to store results (fork -> branch -> patterns)
matching_forks = {}

for fork in forks:
    fork_owner = fork['owner']['login']
    fork_repo = fork['name']
    branches = get_branches(fork_owner, fork_repo)
    print(f'Checking fork: {fork_owner}/{fork_repo}')

    for branch in branches:
        print(f' - Branch: {branch}')
        matched_patterns = set()
        check_directory(fork_owner, fork_repo, branch, DIRECTORY, matched_patterns)

        if matched_patterns:
            fork_key = f'{fork_owner}/{fork_repo}'
            matching_forks.setdefault(fork_key, {})[branch] = matched_patterns

# Write detailed results to a file
with open('find_pref_result.txt', 'w') as f:
    for fork, branches in matching_forks.items():
        f.write(f'{fork}:\n')
        for branch, patterns in branches.items():
            patterns_list = ', '.join(sorted(patterns))
            f.write(f'  - Branch: {branch} | Patterns: {patterns_list}\n')

print("\nDetailed matching results have been saved to 'find_pref_result.txt'.")
