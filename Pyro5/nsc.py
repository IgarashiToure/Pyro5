"""
Name server control tool.

Pyro - Python Remote Objects.  Copyright by Irmen de Jong (irmen@razorvine.net).
"""

import sys
from . import errors
from . import nameserver


def handleCommand(nameserver, cmd, args):
    def printListResult(resultdict, title=""):
        print("--------START LIST %s" % title)
        for name, (uri, metadata) in sorted(resultdict.items()):
            print("%s --> %s" % (name, uri))
            if metadata:
                print("    metadata:", metadata)
        print("--------END LIST %s" % title)

    def cmd_ping():
        nameserver.ping()
        print("Name server ping ok.")

    def cmd_listprefix():
        if len(args) == 0:
            printListResult(nameserver.list(return_metadata=True))
        else:
            printListResult(nameserver.list(prefix=args[0], return_metadata=True), "- prefix '%s'" % args[0])

    def cmd_listregex():
        if len(args) != 1:
            raise SystemExit("requires one argument: pattern")
        printListResult(nameserver.list(regex=args[0], return_metadata=True), "- regex '%s'" % args[0])

    def cmd_lookup():
        if len(args) != 1:
            raise SystemExit("requires one argument: name")
        uri, metadata = nameserver.lookup(args[0], return_metadata=True)
        print(uri)
        if metadata:
            print("metadata:", metadata)

    def cmd_register():
        if len(args) != 2:
            raise SystemExit("requires two arguments: name uri")
        nameserver.register(args[0], args[1], safe=True)
        print("Registered %s" % args[0])

    def cmd_remove():
        if len(args) != 1:
            raise SystemExit("reqiures one argument: name")
        count = nameserver.remove(args[0])
        if count > 0:
            print("Removed %s" % args[0])
        else:
            print("Nothing removed")

    def cmd_removeregex():
        if len(args) != 1:
            raise SystemExit("requires one argument: pattern")
        sure = input("Potentially removing lots of items from the Name server. Are you sure (y/n)?").strip()
        if sure in ('y', 'Y'):
            count = nameserver.remove(regex=args[0])
            print("%d items removed." % count)

    def cmd_setmeta():
        if len(args) < 2:
            raise SystemExit("requires at least 2 arguments: uri and zero or more meta tags")
        metadata = set(args[1:])
        nameserver.set_metadata(args[0], metadata)
        if metadata:
            print("Metadata updated")
        else:
            print("Metadata cleared")

    def cmd_listmeta_all():
        if len(args) < 1:
            raise SystemExit("requires at least one metadata tag argument")
        printListResult(nameserver.list(metadata_all=args, return_metadata=True), " - searched by metadata")

    def cmd_listmeta_any():
        if len(args) < 1:
            raise SystemExit("requires at least one metadata tag argument")
        printListResult(nameserver.list(metadata_any=args, return_metadata=True), " - searched by metadata")

    commands = {
        "ping": cmd_ping,
        "list": cmd_listprefix,
        "listmatching": cmd_listregex,
        "listmeta_all": cmd_listmeta_all,
        "listmeta_any": cmd_listmeta_any,
        "lookup": cmd_lookup,
        "register": cmd_register,
        "remove": cmd_remove,
        "removematching": cmd_removeregex,
        "setmeta": cmd_setmeta
    }
    try:
        commands[cmd]()
    except Exception as x:
        print("Error: %s - %s" % (type(x).__name__, x))


def main(args=None):
    from argparse import ArgumentParser
    parser = ArgumentParser(description="Pyro name server control utility.")
    parser.add_argument("-n", "--host", dest="host", help="hostname of the NS")
    parser.add_argument("-p", "--port", dest="port", type=int,
                        help="port of the NS (or bc-port if host isn't specified)")
    parser.add_argument("-u", "--unixsocket", help="Unix domain socket name of the NS")
    parser.add_argument("-v", "--verbose", action="store_true", dest="verbose", help="verbose output")
    parser.add_argument("command", choices=("list", "lookup", "register", "remove", "removematching", "listmatching",
                        "listmeta_all", "listmeta_any", "setmeta", "ping"))
    args, unknown_args = parser.parse_known_args(args)
    if args.verbose:
        print("Locating name server...")
    if args.unixsocket:
        args.host = "./u:" + args.unixsocket
    try:
        namesrv = nameserver.locateNS(args.host, args.port)
    except errors.PyroError:
        x = sys.exc_info()[1]
        print("Failed to locate the name server: %s" % x)
        return
    if args.verbose:
        print("Name server found: %s" % namesrv._pyroUri)
    handleCommand(namesrv, args.command, unknown_args)
    if args.verbose:
        print("Done.")


if __name__ == "__main__":
    main()