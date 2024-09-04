#!/usr/bin/python3 -u
"""lp2gh.py <lp_bug_id> [options]

This script moves a bug from Launchpad to GitHub. 

The script does the following:
1. Create a new issue in the GitHub repository with the same title, description, and comments.
2. Add a comment in the new GitHub issue with the link to the Launchpad bug.
3. Add appropriate labels to the GitHub issue.
4. Add a comment in the Launchpad bug with the link to the GitHub issue.
5. Add a label to the Launchpad bug with the GitHub issue number.
6. Close the Launchpad bug.

Options:
--github_token <token>          GitHub personal access token. If not provided, the script will use the token from the 'gh' CLI.
--github_repo <repo>            GitHub repository to move the bug to. Default is 'sinanawad/issues_test'.
--github_assignee <assignee>    GitHub user to assign the issue to. If not provided, the script will use the token from the 'gh' CLI.
--do_not_assign                 Do not assign the issue to the assignee provided or taken from 'gh' CLI.
--commit_changes                Commit changes to the GitHub and Launchpad. Default is False.
--i_am_sure                     Confirm that you want to proceed with moving the bug to GitHub. (will not prompt for confirmation)

Prerequisites:
1. To run the script, install the launchpadlib library and make sure the keyring library is installed
            sudo apt install python3-launchpadlib (documentation at https://help.launchpad.net/API/launchpadlib)
            sudo apt install python3-keyring (documentation at https://pypi.org/project/keyring/)

2. Be sure to run it as your username in Launchpad. And that you have the correct keyring configured (e.g. by PYTHON_KEYRING_BACKEND=keyring.backends.SecretService.Keyring)
   Launchpad will prompt you for confirmation the first time you run the script, if you don't already have an OAuth token stored in your keyring.

3. You need the github python library installed.
            sudo apt install python3-github (documentation at https://pygithub.readthedocs.io/en/latest/introduction.html)

4. Either you need to have the 'gh' CLI installed and authenticated with GitHub, or provide your token via command line

5. Make sure you have write permissions to the GitHub repository you are moving the bug to, and to Launchpad to close the bug and add comments.
"""


# Implemenmtation Plan

# [X] Login to Launchpad
# [X] Login to GitHub
# [X] Load Launchpad bug
# [X] Check if bug was already relocated to GitHub - check RELOCATE_LABEL

# Interesting bug details from Launchpad to feed in GitHub
#   Issue Title = Launchpad bug title
#   Issue Description = Launchpad bug description
#   Issue importance = Launchpad bug importance

# [X] Create a new issue in the GitHub repository with the same title, description, and comments.
# [X] Add a comment in the new GitHub issue with the link to the Launchpad bug.
# [X] Add appropriate labels to the GitHub issue.
# [X] Assign the issue to the assignee provided or taken from 'gh' CLI.
# [X] Add a comment in the Launchpad bug with the link to the new GitHub issue, w/ a message for the reporter to go and look at it.
# [X] Add a label to the Launchpad bug with RELOCATE_LABEL.
# [ ] Close the Launchpad bug. Not doing this for now, as it requires the bug supervisor to do it apparently?!


import argparse
import datetime
import sys
import time
import os
import yaml
import subprocess


from launchpadlib.launchpad import Launchpad
from github import Github
from github import Auth

APP_NAME = 'lp2gh'
LP_ENVIRON = 'qastaging'  #'production'
RELOC_TAG = 'relocated-to-github'
GH_TRIAGE_LABEL = 'state/untriaged'
GH_IMPORT_LABEL = 'imported-from-lp'
GH_DEFAULT_REPO = 'sinanawad/utils' #'juju/juju'


global_commit_changes = False
global_github_repo_name = None

def lp_login():
    lp = Launchpad.login_with(APP_NAME, LP_ENVIRON, version='devel')
    self_link = lp.me.self_link
    print(f'LP: Running as: {lp.me.web_link}', file=sys.stderr)
    return lp

def gh_login(gh_user_details):
     # Load GitHub
    gh = Github(gh_user_details[0])
    print(f'GH: Running as {gh_user_details[1]}', file=sys.stderr)
    return gh

def gh_get_user_token_from_cli():
    # Use gh CLI to get the GitHub token
    result = subprocess.run(['gh', 'auth', 'status', '--show-token'], capture_output=True, text=True)
    if result.returncode != 0:
        raise Exception("Failed to get GitHub token using gh CLI")
    
    # Extract the token from the output
    token_line = next(line for line in result.stdout.splitlines() if 'Token:' in line)
    github_token = token_line.split('Token: ')[1].strip()
    token_line = next(line for line in result.stdout.splitlines() if 'account ' in line)
    github_account = token_line.split('account ')[1].strip()
    github_account = github_account.split(' ')[0].strip()
    
    print(f'GH: Token successfully loaded for account {github_account} from gh CLI', file=sys.stderr)
    return github_token, github_account


# Interesting bug details from Launchpad to feed in GitHub
#   Issue Title = Launchpad bug title
#   Issue Description = Launchpad bug description
#   Issue importance = Launchpad bug importance

def gh_create_issue(lp_bug, gh, args):
    global global_commit_changes
    global global_github_repo_name

    gh_issue = None
    gh_repo = gh.get_repo(global_github_repo_name)
    
    print(f'GH: Loaded repo: {gh_repo.full_name}', file=sys.stderr)


    gh_issue_title = 'LP:'+str(lp_bug.id) +' '+lp_bug.title
    if global_commit_changes:
        gh_issue = gh_repo.create_issue(title=gh_issue_title, body=lp_bug.description)
        print(f'GH: Created new issue: {gh_issue.html_url}', file=sys.stderr)
    else:
        print(f'GH: Dry-run: Would create new issue: {lp_bug.title}', file=sys.stderr)
    
    if global_commit_changes:
        bt = lp_bug.bug_tasks[0]
        gh_issue.create_comment(f'This issue was imported from Launchpad by {args.github_assignee} on {datetime.datetime.now()} \nOriginal Launchpad bug: {lp_bug.web_link}\nOriginal Owner: {lp_bug.owner.name}\nOriginal Importance: {bt.importance}')
    print(f'GH: Added Launchpad bug link to issue', file=sys.stderr)
    
    if global_commit_changes:
        gh_issue.add_to_labels(GH_TRIAGE_LABEL)
        gh_issue.add_to_labels(GH_IMPORT_LABEL)
    
    if not args.do_not_assign:
        if global_commit_changes:
            gh_issue.edit(assignees=[args.github_assignee])
        print(f'GH: Assigned issue to {args.github_assignee}', file=sys.stderr)

    return gh_issue


# Add a comment in the Launchpad bug with the link to the new GitHub issue, w/ a message for the reporter to go and look at it.
def lp_update_bug(lp_bug, gh_issue):
    global global_commit_changes
    lp_bug.newMessage(content=f'\t---------\nThis issue has been moved to GitHub: {gh_issue.html_url} on {datetime.datetime.now()}\nPlease visit the link on GitHub to continue the discussion, do not comment here.\n\t---------\n')
    print(f'LP: Added GitHub issue link', file=sys.stderr)
    lp_bug.tags += [RELOC_TAG]
    print(f'LP: Added tag "{RELOC_TAG}"', file=sys.stderr)
    
    # # Only Bug supervisors can change the status to expired
    # bt = lp_bug.bug_tasks[0]
    # bt.status = 'Expired'
    # print('LP: Marked bug as Expired')
    # if global_commit_changes:
    #     bt.lp_save()
   
    if global_commit_changes:
        lp_bug.lp_save()
        print(f'LP: Saved bug', file=sys.stderr)
    else:
        print(f'LP: Bug was NOT saved', file=sys.stderr)


def print_lp_bug_details(lp_bug):
    # Print bug details
    print(f'\tBug ID: {lp_bug.id}')
    print(f'\tTitle: {lp_bug.title}')
    print(f'\tWeb Link: {lp_bug.web_link}')
    print(f'\tOwner: {lp_bug.owner.name}')
    
    # bt = lp_bug.bug_tasks[0]
    # print(f'\tTask: \n\t\ttarget_name {bt.bug_target_name}\n\t\tstatus {bt.status}\n\t\timportance {bt.importance}\n\t\tassignee {bt.assignee.name}\n\t\tmilestone {bt.milestone}')

    # if lp_bug.tags:
    #     print(f'\tTags:')
    #     for tag in lp_bug.tags:
    #         print(f'\t  {tag}')


def main():
    global global_commit_changes  # Declare that we are using the global variable
    global global_github_repo_name  # Declare that we are using the global variable

    
    parser = argparse.ArgumentParser(usage=__doc__)
    parser.add_argument('lp_bug_id', help='Launchpad issue id #',type=int)
    parser.add_argument('--github_token', help='GitHub personal access token', type=str, default="None")
    parser.add_argument('--github_assignee', help='GitHub user to assign the issue to', type=str, default="None")
    parser.add_argument('--github_repo', help='GitHub repository to move the bug to', type=str, default=GH_DEFAULT_REPO)
    parser.add_argument('--do_not_assign', help='Automatically assign the issue to the assignee provided or taken from "gh" CLI', action='store_true')
    parser.add_argument('--commit_changes', help='Commit changes to GitHub and Launchpad or just do a dry-run', action='store_true')
    parser.add_argument('--i_am_sure', help='Confirm that you want to proceed with moving the bug to GitHub', action='store_true')

    args = parser.parse_args()
    global_commit_changes = args.commit_changes

    print("Bootstrapping...")
    if args.github_token == "None":
        try:
            print("GH: No token provided via command-line, attempting fetch from 'gh' CLI")
            gh_user_details = gh_get_user_token_from_cli()
            args.github_token = gh_user_details[0]
            if args.github_assignee == "None":
                args.github_assignee = gh_user_details[1]
            else:
                print(f'GH: Assignee provided via command-line: {args.github_assignee}', file=sys.stderr)
                gh_user_details = (args.github_token, args.github_assignee)
        except Exception as e:
            print(f"Failed to get GitHub token from gh CLI: {e}", file=sys.stderr)
            sys.exit()

    print("\nTool Configuration:")

    if args.github_repo:
        global global_github_repo_name
        print(f'\t> The new GitHub issue will be created in {args.github_repo}')
        global_github_repo_name = args.github_repo

    if args.github_assignee:
        print(f'\t> GitHub assignee is {args.github_assignee}')
    else:
        print("\t> No assignee provided for the GitHub issue.")

    if not args.do_not_assign and args.github_assignee=="None":
        print("Error: GitHub assignee is required when --do_not_assign is not provided.")
        sys.exit()

    if args.do_not_assign:
        print("\t> GitHub issue will NOT be automatically assigned.")
    else:
        print(f'\t> GitHub issue will be automatically assigned to {args.github_assignee}')



    # Launchpad and GitHub login
    print("\nLogging in. Please follow instructions if prompted.")
    lp = lp_login()
    gh = gh_login(gh_user_details)
    print()

    # Load Launchpad bug
    lp_bug = lp.bugs[args.lp_bug_id]
    print("LP: Bug to be relocated:")
    print_lp_bug_details(lp_bug)

    # Check if bug was already relocated to GitHub, if so, exit
    if RELOC_TAG in lp_bug.tags:
        print(f'Bug {lp_bug.id} was already relocated to GitHub! (it has the {RELOC_TAG} tag)', file=sys.stderr)
        sys.exit()

    if global_commit_changes:
        print("!! ATTENTION: Changes will be committed to GitHub and Launchpad. !!\n")
    else:
        print("Changes will NOT be committed, neither to GitHub nor to Launchpad.\n")

    # Ask user to confirm before proceedin by typing 'y', otherwise default is 'N'
    if not args.i_am_sure:
        proceed = input('Proceed with moving the bug to GitHub? [y/N]:')
        if proceed.lower() != 'y':
            print('User opted out.')
            sys.exit()
        else:
            print('\nProceeding...')

    # Create a new issue in the GitHub repository with the same title, description, and comments.
    gh_issue = gh_create_issue(lp_bug, gh, args)
    if gh_issue is None:
        print("GH: Issue was NOT created. Exiting.")
        sys.exit()

    # Add a comment in the Launchpad bug with the link to the new GitHub issue, w/ a message for the reporter to go and look at it.
    lp_update_bug(lp_bug, gh_issue)
    print('Done!', file=sys.stderr)


def no_credential():
    print("Can't proceed without Launchpad credential.")
    sys.exit()

  
if __name__ == '__main__':
	main()
