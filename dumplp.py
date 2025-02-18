#!/usr/bin/python3 -u
import csv
import sys
from launchpadlib.launchpad import Launchpad
import httplib2
httplib2.debuglevel = 1

"""dumplp.py  [options]

This script dumps a set of Launchpad bugs into a CSV file to be imported into a Google sheet or excel later.

The script does the following:
1. Fetches the list of bugs from Launchpad
2. Filters the bugs based on the provided criteria
3. Writes the bugs to a CSV file

Options:
--project <projectName>         Launchpad project including the bugs to be dumped. Default is 'juju'.

Prerequisites:
1. To run the script, install the launchpadlib library and make sure the keyring library is installed
            sudo apt install python3-launchpadlib (documentation at https://help.launchpad.net/API/launchpadlib)

2. Be sure to run it as your username in Launchpad. And that you have the correct keyring configured (e.g. by PYTHON_KEYRING_BACKEND=keyring.backends.SecretService.Keyring)
   Launchpad will prompt you for confirmation the first time you run the script, if you don't already have an OAuth token stored in your keyring.

"""


APP_NAME = 'dumplp'
LP_ENVIRON = 'production'

def lp_login():
    lp = Launchpad.login_with(APP_NAME, LP_ENVIRON, version='devel')
    self_link = lp.me.self_link
    print(f'LP: Running as: {lp.me.web_link}', file=sys.stderr)
    return lp

def fetch_bugs(project_name):
    launchpad = lp_login()
    project = launchpad.projects[project_name]
    print(f'LP: Fetching bugs', file=sys.stderr)
    return project.searchTasks(status=['New', 'Incomplete', 'Confirmed', 'Triaged', 'In Progress', 'Fix Committed'])

def filter_bugs(bugs):
    # Add any filtering logic here if needed
    return bugs

def write_bugs_to_csv(bugs, filename='bugs.csv'):
    print(f'LP: Writing CSV', file=sys.stderr)

    # for bug in bugs:
        # # print(f'{bug.bug.id},{bug.web_link},{bug.title},{bug.status},{bug.importance},{bug.assignee.name if bug.assignee else "Unassigned"},{bug.date_created}')
        # print('>>>>>>>>>>>>>>>>>>>>>>>>>>>')
        # print(f'{bug.web_link},{bug.title},{bug.status},{bug.importance},{bug.date_created}')
        # print('<<<<<<<<<<<<<<<<<<<<<<<<<<<')

    with open(filename, 'w', newline='') as csvfile:
        fieldnames = ['Link', 'Title', 'Status', 'Importance']
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)

        writer.writeheader()
        for bug in bugs:
            writer.writerow({
                'Link': bug.web_link,
                'Title': bug.title,
                'Status': bug.status,
                'Importance': bug.importance,
            })

def main():
    project_name = 'juju'
    if '--project' in sys.argv:
        project_name = sys.argv[sys.argv.index('--project') + 1]

    bugs = fetch_bugs(project_name)
    filtered_bugs = filter_bugs(bugs)
    write_bugs_to_csv(filtered_bugs)

if __name__ == '__main__':
    main()

